"""저장소 — SQLite WAL. jobs · joiners · submissions · events · tokens · server_state.

- 연결은 스레드마다 하나(`threading.local`). autocommit 모드에서 필요한 곳만 `BEGIN IMMEDIATE`.
- `claim` 은 한 트랜잭션 안에서 「queued 이고 그룹이 running/cancelling 잡과 안 겹치는
  가장 작은 id」를 골라 `UPDATE … WHERE state='queued'` 로 잡는다(rowcount 로 원자성 확인).
- 상태 전이는 잡 갱신과 **같은 트랜잭션**에서 `events(kind='state')` 로 남긴다 → `transitions[]`.
- 시작 시 로컬 레인의 `running`·`cancelling` → `lost`, `uploading` → `cancelled`. 큐에서
  사라지는 잡은 없다. 원격 워커(`worker_name`)의 잡은 그대로 두고 heartbeat 시각으로 판정한다.
- 마이그레이션은 `PRAGMA user_version` 으로 번호를 매긴다.
- 시각은 DB 에 epoch 초(REAL)로 두고 모델에서는 UTC aware datetime.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sqlite3
import sys
import threading
import time
import urllib.parse
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from remote_ci_monitor.core import artifacts, outcome
from remote_ci_monitor.core.failures import FailureRow
from remote_ci_monitor.core.model import (
    ACTIVE_STATES,
    BUSY_STATES,
    CANCELLED,
    CANCELLING,
    DEFAULT_POOL,
    FAILED,
    LOST,
    PHASE_MATERIALIZING,
    QUEUED,
    RUNNING,
    SUCCEEDED,
    TERMINAL_STATES,
    TIMED_OUT,
    TOKEN_ADMIN,
    TOKEN_CLIENT,
    TOKEN_KINDS,
    UPLOADING,
    WAITING_STATES,
    CancelInfo,
    Job,
    Joiner,
    Paused,
    Requester,
    Source,
    Transition,
)
from remote_ci_monitor.core.outcome import dump_args, load_args
from remote_ci_monitor.core.progress import Marker
from remote_ci_monitor.core.retention import BlobInfo, BundleInfo

DB_VERSION = 17
#: 제출 capability 의 역할(M5j G5 · 결정 87). 요청자는 잡을 취소하고, 합류자는 자기 참여만 뺀다.
ROLE_CANCEL_JOB = "cancel_job"
ROLE_LEAVE_SUBMISSION = "leave_submission"
#: 마이그레이션 전 자동 백업을 몇 개 남기나(결정 74). 정리는 마이그레이션이 끝난 뒤, 실패는 경고만.
BACKUPS_KEPT = 3
EVENT_STATE = "state"
EVENT_MARKER = "marker"

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  preset TEXT NOT NULL,
  inputs_json TEXT NOT NULL,
  key TEXT NOT NULL,
  concurrency_group TEXT,
  source_json TEXT NOT NULL,
  requester_name TEXT NOT NULL,
  requester_label TEXT NOT NULL,
  state TEXT NOT NULL,
  created_at REAL NOT NULL,
  queued_at REAL,
  started_at REAL,
  finished_at REAL,
  exit_code INTEGER,
  summary TEXT,
  failed_step TEXT,
  failed_step_guessed INTEGER,
  lane INTEGER,
  tree_hash TEXT,
  sha TEXT,
  timeout_seconds INTEGER,
  cancel_requested_at REAL,
  cancel_by TEXT,
  cancel_kill_at REAL,
  cancelled_by TEXT,
  phase TEXT,
  last_output_at REAL,
  join_key TEXT,
  received_bytes INTEGER,
  last_received_at REAL,
  artifacts_purged_at REAL,
  priority INTEGER NOT NULL DEFAULT 0,
  pool TEXT NOT NULL DEFAULT 'default',
  worker_name TEXT,
  summary_code TEXT,
  summary_args TEXT,
  join_count INTEGER NOT NULL DEFAULT 0,
  -- 시작할 때 그 풀에서 돌고 있던 잡 수(자기 포함). 옛 잡은 NULL = 모른다 (M5f)
  concurrent_at_start INTEGER,
  -- 마지막으로 시작한 스텝. `failed_step` 과 달리 인과를 주장하지 않는다 (M5h)
  last_step TEXT,
  -- 실패 이름이 상한(100)을 넘어 버려진 것이 있다 (M5h)
  fail_truncated INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state, id);
CREATE INDEX IF NOT EXISTS jobs_worker ON jobs(worker_name, state);
CREATE INDEX IF NOT EXISTS jobs_pool ON jobs(pool);
CREATE INDEX IF NOT EXISTS jobs_claim ON jobs(state, pool, priority DESC, id);
CREATE INDEX IF NOT EXISTS jobs_recent ON jobs(state, finished_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS jobs_join ON jobs(join_key, state);
CREATE INDEX IF NOT EXISTS jobs_finished ON jobs(finished_at);
CREATE INDEX IF NOT EXISTS jobs_key_finished ON jobs(key, finished_at DESC);
-- 잡이 `::rcm::fail::<이름>` 으로 지목한 것들. 이름 하나가 한 번(같은 잡에서 두 번 찍어도
-- 한 가지 사실이다). `seq` 는 잡이 찍은 순서 — 첫 줄이 대개 진짜 원인이다 (M5h)
CREATE TABLE IF NOT EXISTS job_failures (
  job_id INTEGER NOT NULL,
  name   TEXT    NOT NULL,
  seq    INTEGER NOT NULL,
  PRIMARY KEY (job_id, name)
);
CREATE INDEX IF NOT EXISTS job_failures_name ON job_failures(name);
CREATE TABLE IF NOT EXISTS job_artifacts (
  job_id INTEGER PRIMARY KEY,
  state TEXT NOT NULL,
  policy_json TEXT,
  manifest_json TEXT,
  bundle_sha256 TEXT,
  file_count INTEGER,
  total_bytes INTEGER,
  bundle_bytes INTEGER,
  reserved_bytes INTEGER NOT NULL DEFAULT 0,
  skipped_count INTEGER,
  collected_at REAL,
  ready_at REAL,
  expires_at REAL,
  acked_at REAL,
  purged_at REAL,
  reason_code TEXT,
  reason_args TEXT,
  detail_json TEXT
);
CREATE INDEX IF NOT EXISTS job_artifacts_expiry ON job_artifacts(state, expires_at);
CREATE TABLE IF NOT EXISTS joiners (
  job_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  label TEXT NOT NULL,
  joined_at REAL NOT NULL,
  PRIMARY KEY (job_id, name)
);
CREATE TABLE IF NOT EXISTS submissions (
  job_id INTEGER NOT NULL,
  submission_id TEXT PRIMARY KEY,
  role TEXT NOT NULL,
  capability_hash TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS submissions_job ON submissions(job_id);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL,
  at REAL NOT NULL,
  kind TEXT NOT NULL,
  payload TEXT
);
CREATE INDEX IF NOT EXISTS events_job ON events(job_id, id);
CREATE TABLE IF NOT EXISTS tokens (
  name TEXT PRIMARY KEY,
  sha256 TEXT NOT NULL UNIQUE,
  admin INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL,
  revoked_at REAL,
  kind TEXT NOT NULL DEFAULT 'client'
);
CREATE TABLE IF NOT EXISTS server_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS blobs (
  sha256 TEXT PRIMARY KEY,
  size INTEGER NOT NULL,
  created_at REAL NOT NULL,
  last_used_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS notifications (
  job_id INTEGER NOT NULL,
  notify_name TEXT NOT NULL,
  claimed_at REAL NOT NULL,
  delivered_at REAL,
  failed INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (job_id, notify_name)
);
CREATE TABLE IF NOT EXISTS workers (
  name TEXT PRIMARY KEY,
  pool TEXT NOT NULL,
  lanes INTEGER NOT NULL,
  host_name TEXT,
  version TEXT,
  registered_at REAL NOT NULL,
  last_seen_at REAL NOT NULL
);
"""

_BLOBS_SQL = (
    "CREATE TABLE IF NOT EXISTS blobs (sha256 TEXT PRIMARY KEY, size INTEGER NOT NULL, "
    "created_at REAL NOT NULL, last_used_at REAL NOT NULL)"
)
_WORKERS_SQL = (
    "CREATE TABLE IF NOT EXISTS workers (name TEXT PRIMARY KEY, pool TEXT NOT NULL, "
    "lanes INTEGER NOT NULL, host_name TEXT, version TEXT, registered_at REAL NOT NULL, "
    "last_seen_at REAL NOT NULL)"
)
_NOTIFICATIONS_SQL = (
    "CREATE TABLE IF NOT EXISTS notifications (job_id INTEGER NOT NULL, notify_name TEXT NOT NULL, "
    "claimed_at REAL NOT NULL, delivered_at REAL, failed INTEGER NOT NULL DEFAULT 0, "
    "PRIMARY KEY (job_id, notify_name))"
)


#: v1 → v2: 보존 정리가 산출물을 지운 시각. 기존 DB 에 컬럼만 더한다.
#: v2 → v3(M5): 우선순위 컬럼 · 스냅샷 캐시 blob 표 · 알림 전송 기록.
#: 잡이 만든 파일 묶음(M5e). `jobs.artifacts_purged_at`(M3: 로그·스냅샷·워크스페이스)과 **다른
#: 것**이다 — 섞으면 청소기가 남의 것을 지운다(명세 §1).
_BUNDLES_SQL = (
    "CREATE TABLE IF NOT EXISTS job_artifacts ("
    "job_id INTEGER PRIMARY KEY, state TEXT NOT NULL, policy_json TEXT, manifest_json TEXT, "
    "bundle_sha256 TEXT, file_count INTEGER, total_bytes INTEGER, bundle_bytes INTEGER, "
    "reserved_bytes INTEGER NOT NULL DEFAULT 0, skipped_count INTEGER, collected_at REAL, "
    "ready_at REAL, expires_at REAL, acked_at REAL, purged_at REAL, reason_code TEXT, "
    "reason_args TEXT, detail_json TEXT)"
)
_BUNDLES_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS job_artifacts_expiry ON job_artifacts(state, expires_at)"
)


_MIGRATIONS: dict[int, tuple[str, ...]] = {
    2: ("ALTER TABLE jobs ADD COLUMN artifacts_purged_at REAL",),
    3: (
        "ALTER TABLE jobs ADD COLUMN priority INTEGER NOT NULL DEFAULT 0",
        _BLOBS_SQL,
        _NOTIFICATIONS_SQL,
    ),
    # v3 → v4(M5b): 잡이 어느 풀의 워커에서 도는가. 옛 행은 전부 기본 풀.
    4: ("ALTER TABLE jobs ADD COLUMN pool TEXT NOT NULL DEFAULT 'default'",),
    # v4 → v5(M5b-2): 토큰 종류 · 원격 워커 표 · 잡을 claim 한 워커 이름. 옛 admin=1 은 kind admin.
    5: (
        "ALTER TABLE tokens ADD COLUMN kind TEXT NOT NULL DEFAULT 'client'",
        "UPDATE tokens SET kind='admin' WHERE admin=1",
        "ALTER TABLE jobs ADD COLUMN worker_name TEXT",
        "CREATE INDEX IF NOT EXISTS jobs_worker ON jobs(worker_name, state)",
        "CREATE INDEX IF NOT EXISTS jobs_pool ON jobs(pool)",  # list_pools 가 status 마다 돈다
        _WORKERS_SQL,
    ),
    # v5 → v6(M5d-0): 서버가 만든 요약의 코드와 원시 인자(결정 37). 옛 행은 코드가 없다 —
    # 화면은 저장된 문장으로 물러선다.
    6: (
        "ALTER TABLE jobs ADD COLUMN summary_code TEXT",
        "ALTER TABLE jobs ADD COLUMN summary_args TEXT",
    ),
    # v6 → v7(M5e): 합류 횟수(삭제 규칙 · 결정 40)와 잡 산출물 묶음. 옛 잡은 join_count 0 이고
    # 묶음 행이 없다 — 공개 상태로는 `unknown` 이지 `empty` 가 아니다(명세 §3 · §9).
    7: (
        "ALTER TABLE jobs ADD COLUMN join_count INTEGER NOT NULL DEFAULT 0",
        _BUNDLES_SQL,
        _BUNDLES_INDEX_SQL,
    ),
    # v7 → v8(M5f): claim 전용 인덱스. 없으면 플래너가 `jobs_pool`(풀 전체)을 타고 ORDER BY 를
    # 임시 B-tree 로 푼다 — 20만 행에서 8.5 ms 이고, claim 은 레인마다 초당 두 번 BEGIN IMMEDIATE
    # 안에서 돈다. 통계(ANALYZE)로 고치지 않는 이유: 통계는 이미 열린 커넥션에 반영되지 않고
    # (`_conn()` 은 스레드 로컬이라 로컬 레인 스레드는 커넥션을 안 닫는다) 임시 B-tree 도 안 없앤다.
    8: ("CREATE INDEX IF NOT EXISTS jobs_claim ON jobs(state, pool, priority DESC, id)",),
    # v8 → v9(M5f): 최근 완료 잡. `state IN (…)` 때문에 `jobs_finished` 를 못 타고 종료 잡
    # **전체**를 정렬한다 — 여덟 개를 고르려고 5만 개를 줄 세운다(7.1 ms). 커버링 인덱스면
    # 0.009 ms 다. 중앙값을 요청 경로에서 뗀 뒤 이게 `/api/status` 의 지배항이 됐다.
    9: ("CREATE INDEX IF NOT EXISTS jobs_recent ON jobs(state, finished_at DESC, id DESC)",),
    # v9 → v10(M5f): 시작할 때 그 풀에서 몇 개가 돌고 있었나. 중앙값은 혼자 잰 것이라 같이
    # 도는 동안은 예상보다 오래 걸리는데, 얼마나 그런지는 **표본이 있어야** 안다. 지금 안
    # 모으면 소급해서 못 얻는다. 옛 잡은 NULL — 0(「혼자 돌았다」)이 아니라 **모른다** 다.
    10: ("ALTER TABLE jobs ADD COLUMN concurrent_at_start INTEGER",),
    # v10 → v11: `failed_step` 이 확정인가 추측인가. 마이그레이션 전에 끝난 잡은 **NULL = 모름**
    # 이다 — 0 으로 채우면 그때의 추측이 「확정」으로 둔갑한다(2026-09-08 운영 사고).
    # ⚠️ M5h(결정 63) 뒤로 이 열은 **안 쓴다** — 추측을 아예 안 하므로 늘 거짓이 된다.
    #    지우는 마이그레이션을 따로 두지 않는 이유: 열 하나가 남는 비용보다 되돌릴 여지를
    #    남기는 값이 크다(두 설계 중 하나를 고르는 일은 오너의 것이다).
    11: ("ALTER TABLE jobs ADD COLUMN failed_step_guessed INTEGER",),
    # v11 → v12(M5h): 마지막으로 시작한 스텝. `failed_step` 이 「선언된 것만」이 되면서
    # 「끝났을 때 어디였나」를 말할 칸이 필요해졌다. 옛 잡은 NULL = 모른다.
    12: ("ALTER TABLE jobs ADD COLUMN last_step TEXT",),
    # v12 → v13(M5h): 실패 이름 대장. 이름별 최근 이력(`GET /jobs/{id}` 의 `failures[]`)이
    # 이 표 위에 선다. 창 질의가 `(key, finished_at)` 을 타야 해서 인덱스도 같이 만든다.
    13: (
        "CREATE TABLE IF NOT EXISTS job_failures ("
        " job_id INTEGER NOT NULL, name TEXT NOT NULL, seq INTEGER NOT NULL,"
        " PRIMARY KEY (job_id, name))",
        "CREATE INDEX IF NOT EXISTS job_failures_name ON job_failures(name)",
        "CREATE INDEX IF NOT EXISTS jobs_key_finished ON jobs(key, finished_at DESC)",
        "ALTER TABLE jobs ADD COLUMN fail_truncated INTEGER NOT NULL DEFAULT 0",
    ),
    # v13 → v14(M5h): 옛 코드는 취소·유실 잡에도 실패 스텝을 남겼다(운영 잡 #176 — 사람이 세운
    # 잡에 「이게 깨졌다」로 읽히는 라벨이 붙었다). 그 라벨은 증거가 아니라 **추론의 부산물**이라
    # 지운다. 표시도 같은 규칙을 강제하지만(결정 64) JSON 을 읽는 래퍼까지 고쳐 준다.
    14: ("UPDATE jobs SET failed_step=NULL WHERE state IN ('cancelled','lost')",),
    # v14 → v15(M5h): 옛 실패 잡의 라벨을 **덜 주장하는 칸으로 옮긴다**. 그 값은 대개 추론값
    # (「마지막으로 시작한 스텝」)이고, 선언값이었는지는 이제 와서 구분할 수 없다 — 그래서
    # 인과를 주장하는 `failed_step` 이 아니라 자리만 말하는 `last_step` 에 둔다. 안 그러면
    # 신고자가 #162 를 다시 열었을 때 **고쳤다는 그 문자열을 그대로** 본다.
    # 새 코드가 쓴 행은 라벨이 있으면 `last_step` 도 항상 있어서 이 조건에 안 걸린다.
    15: (
        "UPDATE jobs SET last_step=failed_step, failed_step=NULL "
        "WHERE state IN ('failed','timed_out') AND failed_step IS NOT NULL "
        "AND last_step IS NULL",
    ),
    # v15 → v16(M5i · 결정 78): 2026-09-10 사고의 뒤처리. 새 코드가 운영 DB 를 15 로 올린 뒤에도
    # 옛 빌드(0.2.5)가 계속 돌며 실패 잡에 **추론 라벨**을 `failed_step` 에 썼다(#196 · #199 ·
    # #200). v15 는 다시 돌지 않으니 그냥 올리면 그 라벨이 「선언된 실패」로 보인다. 새 코드는 선언
    # 라벨을 쓸 때 언제나 `job_failures` 행을 같은 트랜잭션에 남기므로(로컬·원격 둘 다
    # `outcome_for()` 경로 · `timed_out` 도 · 상한에 잘려도 최소 한 행), 「라벨은 있는데 대장 행이
    # 하나도 없다」가 옛 빌드의 흔적이다. 이름 일치로 가리지 않는 이유: 100개 상한에 잘린 잡은
    # 라벨이 대장의 어느 이름과도 다를 수 있다. 한 번짜리다 — 매 기동 보정은 안 한다.
    16: (
        "UPDATE jobs SET last_step=COALESCE(last_step, failed_step), failed_step=NULL "
        "WHERE state IN ('failed','timed_out') AND failed_step IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM job_failures WHERE job_failures.job_id=jobs.id)",
        "UPDATE jobs SET failed_step=NULL, last_step=NULL WHERE state IN ('cancelled','lost')",
    ),
    # v16 → v17(M5j G5 · 결정 87): 제출 참여자별 취소 capability. `POST /jobs` 시도마다(합류
    # 포함) 행 하나 — 서버는 비밀의 SHA-256 만 둔다. joiner 표의 PK `(job_id, name)` 은 같은
    # 토큰 이름의 세션들을 못 가르지만 이 표는 시도마다 다르다. 옛 잡은 행이 없다 — 강제 모드
    # (`cancel_requires_submission_token`)에서는 admin 만 취소할 수 있고, 아니면 오늘 규칙이다.
    # v16 백업은 `migrate()` 의 경계에서 이 DDL 보다 먼저 만들어진다(결정 74).
    17: (
        "CREATE TABLE IF NOT EXISTS submissions ("
        " job_id INTEGER NOT NULL, submission_id TEXT PRIMARY KEY, role TEXT NOT NULL,"
        " capability_hash TEXT NOT NULL, created_at REAL NOT NULL)",
        "CREATE INDEX IF NOT EXISTS submissions_job ON submissions(job_id)",
    ),
}


def _outcome(code: str, **args: Any) -> dict[str, Any]:
    """`_set_state` 에 바로 넣을 요약 세 값(문장·코드·인자 JSON). 결정 37."""
    text, code, clean = outcome.summary(code, **args)
    return {"summary": text, "summary_code": code, "summary_args": dump_args(clean)}


def _ro_uri(path: Path) -> str:
    """살아 있는 DB 를 읽기만 하는 URI — 절대경로 · URL 인코딩 · `mode=ro`. `immutable=1` 은 안 쓴다
    (WAL 을 무시해 다른 프로세스가 쓰는 중인 DB 를 깨진 것처럼 읽는다)."""
    return f"file:{urllib.parse.quote(str(Path(path).resolve()))}?mode=ro"


def database_version(path: Path) -> int:
    """파일을 바꾸지 않고 `PRAGMA user_version` 만 읽는다. 없거나 비어 있으면 0."""
    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        return 0
    # ⚠️ 읽기 전용 연결은 WAL DB 옆의 `-wal`·`-shm` 이 없으면 만들고 닫을 때 못 지운다(0바이트 ·
    # 무해 — 다음 보통 연결이 치운다). 그래도 지우지 않는다: 서버가 막 뜨는 중이면 그 파일은 서버의
    # 것이고, 「읽기만 한다」는 명령이 데이터 디렉터리에서 무엇을 지우는 일은 없어야 한다.
    conn = sqlite3.connect(_ro_uri(path), uri=True)
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


class CopyDeadlineExceeded(RuntimeError):
    """페이지 단위 복사가 마감 안에 못 끝났다 — 「모른다」이지 실패가 아니다."""


def _copy_database(
    src: Path, dst: Path, *, deadline: float | None = None, pages: int = 256
) -> None:
    """`Connection.backup()` 으로 일관된 사본을 뜬다(WAL 에만 있는 쓰기까지). 원본은 `mode=ro`.

    `deadline` 은 `time.monotonic()` 값이다 — 페이지 묶음마다 확인하고 넘으면
    `CopyDeadlineExceeded`. 반쯤 된 사본은 부르는 쪽이 치운다.
    """

    def check(status: int, remaining: int, total: int) -> None:
        if deadline is not None and time.monotonic() > deadline:
            raise CopyDeadlineExceeded(f"{remaining} of {total} pages left")

    if deadline is not None and time.monotonic() > deadline:
        raise CopyDeadlineExceeded("before the first page")
    source = sqlite3.connect(_ro_uri(src), uri=True)
    try:
        target = sqlite3.connect(str(dst))
        try:
            source.backup(target, pages=pages, progress=check)
        finally:
            target.close()
    finally:
        source.close()


def _verify_copy(path: Path, expected_version: int) -> None:
    """사본이 열리고 무결하며 옛 버전 표식을 갖는지. 아니면 예외.

    보통 연결로 연다(`mode=ro` 가 아니라) — 읽기 전용 연결은 WAL DB 옆에 만든 `-wal`·`-shm` 을
    닫을 때 못 지워 백업 디렉터리에 찌꺼기가 남는다. 읽기만 하므로 내용은 안 바뀐다.
    """
    conn = sqlite3.connect(str(path))
    try:
        (ok,) = conn.execute("PRAGMA integrity_check").fetchone()
        if ok != "ok":
            raise sqlite3.DatabaseError(f"integrity_check: {ok}")
        (version,) = conn.execute("PRAGMA user_version").fetchone()
        if int(version) != expected_version:
            raise sqlite3.DatabaseError(f"user_version {version} != {expected_version}")
    finally:
        conn.close()


def backup_dir(path: Path) -> Path:
    return Path(path).parent / "backup"


def backup_path(path: Path, version: int) -> Path:
    """`<data_dir>/backup/rcm.sqlite3.v<version>.bak` — 그 버전에서 올리기 직전의 사본."""
    return backup_dir(path) / f"{Path(path).name}.v{version}.bak"


def newer_database_message(path: Path, version: int) -> str:
    """옛 빌드가 새 DB 를 거절할 때의 문장 — 거절만 하지 않고 길을 붙인다(B2-3)."""
    return (
        f"database schema version {version} is newer than this build ({DB_VERSION}) — "
        f"stop the service, restore {backup_path(path, DB_VERSION)} "
        f"(and remove {Path(path).name}-wal/-shm), or upgrade"
    )


class StoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class SampleRow:
    """중앙값 계산에 필요한 것만 담은 가벼운 행(`Store.list_sample_rows`).

    `core/queue.medians_from` 과 `split_by_pool` 이 읽는 여섯 칸이 전부다 — `Job` 을 흉내 내지
    않고, 필요한 칸이 늘면 여기와 두 함수가 같이 바뀐다.
    """

    key: str
    pool: str
    state: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class LaneBusy(StoreError):
    """그 워커의 그 레인에 이미 running·cancelling 잡이 있다 — 레인 과할당 금지(M5b-2)."""


@dataclass(frozen=True)
class TokenInfo:
    name: str
    admin: bool
    created_at: datetime
    revoked_at: datetime | None = None
    kind: str = TOKEN_CLIENT  # client | admin | worker — `admin` 불리언과 항상 일치(M5b-2)


@dataclass(frozen=True)
class WorkerRow:
    """원격 워커 등록 행. 상태(up/down)는 `last_seen_at` 과 서버 시각으로만 판정한다."""

    name: str
    pool: str
    lanes: int
    host_name: str | None
    version: str | None
    registered_at: datetime
    last_seen_at: datetime


def _ts(dt: datetime | None) -> float | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


def _dt(ts: float | None) -> datetime | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=UTC)


_ADD_COLUMN_RE = re.compile(r"^\s*ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(\w+)\b", re.I)


def _already_added(conn: sqlite3.Connection, stmt: str) -> bool:
    """`ALTER TABLE … ADD COLUMN` 이 더하려는 열이 이미 있는가.

    마이그레이션 번호는 옮겨질 수 있다 — `dev` 가 같은 번호를 먼저 가져가면 이쪽이 뒤로 밀린다.
    그 사이 옛 빌드로 연 데이터베이스는 **낮은 번호인데 열은 이미 있는** 상태가 되고, 그대로
    두면 `duplicate column name` 으로 죽는다. 버전이 안 올라가니 다음에도 똑같이 죽어 서버가
    영영 안 뜬다. 열을 더하는 것은 본래 멱등한 일이라 이미 있으면 건너뛴다.
    """
    m = _ADD_COLUMN_RE.match(stmt)
    if m is None:
        return False
    table, column = m.group(1), m.group(2)
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def _opt_bool(v: Any) -> bool | None:
    """SQLite 의 0/1/NULL → True/False/None. NULL 은 「모름」이라 False 로 접지 않는다."""
    return None if v is None else bool(v)


def hash_token(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _bundle_manifest(result: Any) -> str | None:
    """`CollectResult` → `manifest_json`. 파일 목록은 보호 라우트에서만 나간다(§10)."""
    files = getattr(result, "files", ()) or ()
    if not files:
        return None
    return json.dumps(
        {
            "bundle_sha256": result.bundle_sha256,
            "files": [
                {"path": f.path, "size": f.size, "sha256": f.sha256, "mode": f.mode} for f in files
            ],
        },
        separators=(",", ":"),
    )


def _upsert_bundle(
    conn: sqlite3.Connection,
    job_id: int,
    result: Any,
    *,
    now: datetime,
    ttl_hours: int = 24,
    expires_at: datetime | None = None,
) -> None:
    """묶음 행 하나를 쓴다(열려 있는 트랜잭션 안에서). 이미 발행된 묶음은 **불변**이다.

    만료는 `ready` 일 때만 박는다 — 버리거나 깨진 묶음은 지울 파일이 없다. 발행된 묶음은 만료가
    반드시 있다(만료 없는 행을 만들면 sweep 이 영영 못 가져간다, §9 ⑤).
    """
    row = conn.execute(
        "SELECT state, bundle_sha256 FROM job_artifacts WHERE job_id=?", (job_id,)
    ).fetchone()
    if row is not None and row["bundle_sha256"] and row["state"] == artifacts.READY:
        return  # 불변 — 두 번째 finish 가 발행된 묶음을 갈아치우지 않는다
    ready = result.state == artifacts.READY
    # 수를 아는 것은 수집이 끝까지 간 경우다 — `empty` 의 0 은 「모았는데 없었다」는 사실이고
    # `dropped`·`failed` 의 None 은 「모른다」다. 둘을 섞으면 §10 의 정직성이 깨진다.
    known = result.state in (artifacts.READY, artifacts.EMPTY)
    expiry = expires_at if expires_at is not None else artifacts.expires_at(now, ttl_hours)
    conn.execute(
        "INSERT INTO job_artifacts (job_id, state, manifest_json, bundle_sha256, file_count, "
        "total_bytes, bundle_bytes, reserved_bytes, skipped_count, collected_at, ready_at, "
        "expires_at, reason_code, reason_args, detail_json) "
        "VALUES (?,?,?,?,?,?,?,0,?,?,?,?,?,?,?) "
        "ON CONFLICT(job_id) DO UPDATE SET state=excluded.state, "
        "manifest_json=excluded.manifest_json, bundle_sha256=excluded.bundle_sha256, "
        "file_count=excluded.file_count, total_bytes=excluded.total_bytes, "
        "bundle_bytes=excluded.bundle_bytes, reserved_bytes=0, "
        "skipped_count=excluded.skipped_count, collected_at=excluded.collected_at, "
        "ready_at=excluded.ready_at, expires_at=excluded.expires_at, "
        "reason_code=excluded.reason_code, reason_args=excluded.reason_args, "
        "detail_json=excluded.detail_json",
        (
            job_id,
            result.state,
            _bundle_manifest(result),
            result.bundle_sha256,
            len(result.files or ()) if known else None,
            result.total_bytes if known else None,
            result.bundle_bytes if known else None,
            result.skipped_count,
            _ts(now),
            _ts(now) if ready else None,
            _ts(expiry) if ready else None,
            result.reason_code,
            outcome.dump_args(result.reason_args),
            json.dumps(result.detail, separators=(",", ":")) if result.detail else None,
        ),
    )


class Store:
    """SQLite 저장소. 한 프로세스 안에서 여러 스레드가 같이 쓴다."""

    def __init__(self, path: str | Path, *, log: Callable[[str], None] | None = None):
        self.path = Path(path)
        self._local = threading.local()
        self._lock = threading.Lock()
        self.open_connections = 0  # 지금 열린 연결 수(스레드마다 하나) — 누수 감시
        self._holds: dict[int, int] = {}  # 지금 내려보내는 중인 묶음(M5e) — 프로세스 안에서만
        # 경고 한 줄을 어디에 쓰나(백업 정리 실패 같은, 멈출 일은 아닌 것). 기본은 stderr.
        self._log = log or (lambda msg: print(msg, file=sys.stderr))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    # ── 연결 ────────────────────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.path), timeout=5.0, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
            with self._lock:
                self.open_connections += 1
        return conn

    def close(self) -> None:
        """이 스레드의 연결을 닫는다. 요청 스레드는 끝날 때 꼭 부른다 — 스레드가 죽어도 연결은
        스스로 닫히지 않아 파일 핸들이 쌓였다(실배치: 252개 → 'Too many open files' → 전부 500)."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None
            with self._lock:
                self.open_connections -= 1

    def migrate(self) -> None:
        """`PRAGMA user_version` 기준으로 빠진 마이그레이션만 적용한다.

        실제로 버전을 올릴 때(`1 ≤ version < DB_VERSION`)는 **어떤 변경보다 먼저** — `_conn()` 의
        `journal_mode=WAL` 보다도 먼저 — 옛 DB 의 검증된 사본을 `backup/` 에 남긴다(결정 74). 못
        남기면 마이그레이션을 시작하지 않는다. 이것이 「옛 빌드가 새 DB 를 거절한다」의 복구 경로다.
        """
        version = database_version(self.path)
        if 1 <= version < DB_VERSION:
            self._backup_before_migration(version)
        conn = self._conn()
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 1:
            # executescript 는 트랜잭션을 먼저 COMMIT 해 버리므로 문장 단위로 실행한다.
            # 새 DB 는 최신 스키마를 한 번에 만든다(중간 버전을 거치지 않는다).
            conn.execute("BEGIN IMMEDIATE")
            try:
                for stmt in _SCHEMA_V1.split(";"):
                    if stmt.strip():
                        conn.execute(stmt)
                conn.execute(f"PRAGMA user_version={DB_VERSION}")
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        elif version < DB_VERSION:
            for target in range(version + 1, DB_VERSION + 1):
                conn.execute("BEGIN IMMEDIATE")
                try:
                    for stmt in _MIGRATIONS[target]:
                        if _already_added(conn, stmt):
                            continue  # 더하려는 열이 이미 있다 — 할 일이 없다
                        conn.execute(stmt)
                    conn.execute(f"PRAGMA user_version={target}")
                    conn.execute("COMMIT")
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
            self._prune_backups()
        elif version > DB_VERSION:
            raise StoreError(newer_database_message(self.path, version))

    def _backup_before_migration(self, old: int) -> None:
        """`backup/rcm.sqlite3.v<old>.bak` — 임시 파일로 뜨고, 열어서 검증하고, 원자적으로 이름을
        바꾼다. 어느 단계든 실패하면 `StoreError`: DB 는 아직 아무것도 안 바뀌었다(fail-closed)."""
        final = backup_path(self.path, old)
        tmp = final.with_name(final.name + ".tmp")
        try:
            final.parent.mkdir(parents=True, exist_ok=True)
            _copy_database(self.path, tmp)
            _verify_copy(tmp, old)
            os.replace(tmp, final)
        except Exception as e:  # noqa: BLE001 — 원인이 무엇이든 마이그레이션을 시작하면 안 된다
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise StoreError(
                f"migration backup failed ({type(e).__name__}: {e}) — the database was not "
                f"changed. Free space or fix {final.parent}, then start again"
            ) from e

    def _prune_backups(self) -> None:
        """자동 백업은 최근 `BACKUPS_KEPT` 개만. 사람이 만든 파일(`.pre-0.2.4.bak` 같은)은 안 본다.
        정리 실패는 경고만 — 마이그레이션은 이미 끝났다."""
        pattern = re.compile(rf"^{re.escape(self.path.name)}\.v(\d+)\.bak$")
        found: list[tuple[int, Path]] = []
        with contextlib.suppress(OSError):
            for p in backup_dir(self.path).iterdir():
                m = pattern.match(p.name)
                if m:
                    found.append((int(m.group(1)), p))
        found.sort()
        for _version, p in found[:-BACKUPS_KEPT] if len(found) > BACKUPS_KEPT else []:
            try:
                p.unlink()
            except OSError as e:
                self._log(f"warning: could not remove old backup {p.name}: {e}")

    def user_version(self) -> int:
        return int(self._conn().execute("PRAGMA user_version").fetchone()[0])

    def healthy(self) -> bool:
        try:
            self._conn().execute("SELECT 1 FROM jobs LIMIT 1").fetchall()
            return True
        except sqlite3.Error:
            return False

    # ── 잡 읽기 ─────────────────────────────────────────────────────────────

    def _row_to_job(self, conn: sqlite3.Connection, row: sqlite3.Row) -> Job:
        src = json.loads(row["source_json"])
        source = Source(
            mode=src.get("mode", "tree"),
            repo=src.get("repo"),
            branch=src.get("branch"),
            base_sha=src.get("base_sha"),
            dirty=src.get("dirty"),
            tree_hash=src.get("tree_hash"),
            bytes=src.get("bytes"),
            received_bytes=row["received_bytes"],
            last_received_at=_dt(row["last_received_at"]),
            ref=src.get("ref"),
            sha=src.get("sha"),
            uploaded_bytes=src.get("uploaded_bytes"),
            cached_bytes=src.get("cached_bytes"),
        )
        joiners = tuple(
            Joiner(name=j["name"], label=j["label"], joined_at=_dt(j["joined_at"]))
            for j in conn.execute(
                "SELECT name, label, joined_at FROM joiners WHERE job_id=? "
                "ORDER BY joined_at, name",
                (row["id"],),
            )
        )
        transitions = tuple(
            Transition(state=json.loads(e["payload"])["state"], at=_dt(e["at"]))
            for e in conn.execute(
                "SELECT at, payload FROM events WHERE job_id=? AND kind=? ORDER BY id",
                (row["id"], EVENT_STATE),
            )
        )
        cancel = None
        if row["cancel_requested_at"] is not None:
            cancel = CancelInfo(
                requested_at=_dt(row["cancel_requested_at"]),
                by=row["cancel_by"] or "",
                kill_at=_dt(row["cancel_kill_at"]),
            )
        return Job(
            id=row["id"],
            preset=row["preset"],
            inputs=json.loads(row["inputs_json"]),
            key=row["key"],
            concurrency_group=row["concurrency_group"],
            source=source,
            requester=Requester(name=row["requester_name"], label=row["requester_label"]),
            state=row["state"],
            created_at=_dt(row["created_at"]),
            queued_at=_dt(row["queued_at"]),
            started_at=_dt(row["started_at"]),
            finished_at=_dt(row["finished_at"]),
            exit_code=row["exit_code"],
            summary=row["summary"],
            summary_code=row["summary_code"],
            summary_args=load_args(row["summary_args"]),
            failed_step=row["failed_step"],
            last_step=row["last_step"],
            fail_truncated=bool(row["fail_truncated"]),
            lane=row["lane"],
            timeout_seconds=row["timeout_seconds"],
            cancel=cancel if row["state"] == CANCELLING else None,
            cancelled_by=row["cancelled_by"],
            phase=row["phase"],
            last_output_at=_dt(row["last_output_at"]),
            joiners=joiners,
            transitions=transitions,
            artifacts_purged_at=_dt(row["artifacts_purged_at"]),
            priority=int(row["priority"] or 0),
            pool=row["pool"] or DEFAULT_POOL,
            worker_name=row["worker_name"],
        )

    def get_job(self, job_id: int) -> Job | None:
        conn = self._conn()
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._row_to_job(conn, row) if row else None

    def _jobs(self, sql: str, params: tuple[Any, ...] = ()) -> list[Job]:
        conn = self._conn()
        return [self._row_to_job(conn, r) for r in conn.execute(sql, params).fetchall()]

    def list_active(self) -> list[Job]:
        marks = ",".join("?" * len(ACTIVE_STATES))
        return self._jobs(
            f"SELECT * FROM jobs WHERE state IN ({marks}) ORDER BY id", tuple(sorted(ACTIVE_STATES))
        )

    def list_recent(self, limit: int) -> list[Job]:
        """최근 완료 잡 `limit` 개, 새것부터.

        **두 단계로 고른다.** `SELECT *` 한 방이면 `jobs_recent` 가 커버링이 아니게 돼 플래너가
        `jobs_state` 로 물러서고 종료 잡 **전체**를 정렬한다 — 여덟 개를 고르려고 5만 개를 줄
        세우는 셈이라 22.5 ms 다. id 만 커버링 인덱스로 고른 뒤 그 행만 읽으면 0.02 ms 다.
        """
        if limit <= 0:
            return []
        marks = ",".join("?" * len(TERMINAL_STATES))
        ids = [
            int(r["id"])
            for r in self._conn().execute(
                f"SELECT id FROM jobs WHERE state IN ({marks}) "
                "ORDER BY finished_at DESC, id DESC LIMIT ?",
                (*sorted(TERMINAL_STATES), limit),
            )
        ]
        if not ids:
            return []
        found = {
            j.id: j
            for j in self._jobs(
                f"SELECT * FROM jobs WHERE id IN ({','.join('?' * len(ids))})", tuple(ids)
            )
        }
        return [found[i] for i in ids if i in found]  # 고른 순서를 지킨다

    def active_worker_lanes(self) -> dict[tuple[str, int], tuple[int, datetime | None]]:
        """`(워커, 레인) → (잡 id, 시작 시각)` — 원격 레인의 busy 판정에 필요한 전부.

        워커마다 `jobs_of_worker` 를 돌면(옛 방식) 워커 수 × (1 + 잡마다 서브쿼리 둘)이 되고,
        이 조회는 상태 문서마다 그리고 **마커 줄마다** 돈다 — 워커 50 · 실행 250 에서 한 줄에
        SQL 555개였다. `WorkerInfo` 가 쓰는 것은 잡 id 와 시작 시각뿐이다(M5f 결정 49).
        """
        busy = ",".join("?" * len(BUSY_STATES))
        rows = (
            self._conn()
            .execute(
                f"SELECT worker_name, lane, id, started_at FROM jobs "
                f"WHERE worker_name IS NOT NULL AND lane IS NOT NULL AND state IN ({busy}) "
                "ORDER BY id",
                tuple(sorted(BUSY_STATES)),
            )
            .fetchall()
        )
        return {
            (r["worker_name"], int(r["lane"])): (int(r["id"]), _dt(r["started_at"])) for r in rows
        }

    def list_sample_rows(self, since: datetime) -> list[SampleRow]:
        """중앙값이 읽는 여섯 칸만. 행마다 `Job` 을 만들지 않는다.

        `_row_to_job` 은 행마다 joiners·events 서브쿼리를 돌고 JSON 을 두 번 판다. 45일치를
        그렇게 읽으면 `/api/status` 가 보존된 잡 수에 선형으로 끌려간다(1만 행 168 ms) —
        그런데 `medians_from` 이 보는 것은 `key · pool · state · created_at · started_at ·
        finished_at` 여섯 개뿐이다(M5f 결정 49).
        """
        marks = ",".join("?" * len(TERMINAL_STATES))
        rows = (
            self._conn()
            .execute(
                f"SELECT key, pool, state, created_at, started_at, finished_at FROM jobs "
                f"WHERE state IN ({marks}) AND started_at >= ? AND finished_at IS NOT NULL "
                "ORDER BY id",
                (*sorted(TERMINAL_STATES), _ts(since)),
            )
            .fetchall()
        )
        return [
            SampleRow(
                key=r["key"],
                pool=r["pool"] or DEFAULT_POOL,
                state=r["state"],
                created_at=_dt(r["created_at"]),
                started_at=_dt(r["started_at"]),
                finished_at=_dt(r["finished_at"]),
            )
            for r in rows
        ]

    def list_samples(self, since: datetime) -> list[Job]:
        """표본 후보: 시작·종료 시각이 있는 종료 잡. 정책 필터는 순수 계층이 한다."""
        marks = ",".join("?" * len(TERMINAL_STATES))
        return self._jobs(
            f"SELECT * FROM jobs WHERE state IN ({marks}) AND started_at >= ? "
            "AND finished_at IS NOT NULL ORDER BY id",
            (*sorted(TERMINAL_STATES), _ts(since)),
        )

    def list_unpurged_finished(self, limit: int = 1000) -> list[Job]:
        """보존 정리 후보: 산출물을 아직 안 지운 종료 잡. 오래 끝난 것부터."""
        marks = ",".join("?" * len(TERMINAL_STATES))
        return self._jobs(
            f"SELECT * FROM jobs WHERE state IN ({marks}) AND artifacts_purged_at IS NULL "
            "ORDER BY COALESCE(finished_at, created_at), id LIMIT ?",
            (*sorted(TERMINAL_STATES), limit),
        )

    def mark_artifacts_purged(self, job_ids: Iterable[int], now: datetime) -> int:
        """산출물을 지웠다고 표시한다. 종료 잡만, 아직 표시 안 된 것만. 표시한 수를 돌려준다."""
        ids = [int(i) for i in job_ids]
        if not ids:
            return 0
        marks = ",".join("?" * len(TERMINAL_STATES))
        id_marks = ",".join("?" * len(ids))
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            cur = conn.execute(
                f"UPDATE jobs SET artifacts_purged_at=? WHERE id IN ({id_marks}) "
                f"AND state IN ({marks}) AND artifacts_purged_at IS NULL",
                (_ts(now), *ids, *sorted(TERMINAL_STATES)),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return int(cur.rowcount)

    # ── 실패 이름 대장 (M5h) ────────────────────────────────────────────────

    def failure_stats(
        self, job_id: int, key: str, finished_at: datetime | None, *, window: int
    ) -> tuple[list[FailureRow], int, int]:
        """이 잡이 지목한 이름들이 **같은 key 의 최근 창**에서 몇 번 보였나.

        창은 `finished_at` 을 **앵커로** 그 잡까지 최근 `window` 개다(취소·유실은 아무 말도
        안 하므로 뺀다). 이 잡이 늘 창의 맨 앞이라 자기 이름의 `seen` 은 1 이상이고, 한 달
        뒤에 같은 잡을 다시 열어도 **같은 답**이 나온다(창이 흘러가지 않는다 — 명세 §2.1).

        돌려주는 것은 `(줄 목록, 창의 잡 수, 이름 없이 실패한 잡 수)`. 줄 순서는 그 잡이 찍은
        순서(`seq`)다.
        """
        conn = self._conn()
        at = _ts(finished_at) if finished_at is not None else None
        if at is None or window < 1:
            return [], 0, 0
        states = (SUCCEEDED, FAILED, TIMED_OUT)
        marks = ",".join("?" * len(states))
        ids = [
            int(r[0])
            for r in conn.execute(
                f"SELECT id FROM jobs WHERE key=? AND state IN ({marks}) "
                "AND finished_at IS NOT NULL "
                "AND (finished_at < ? OR (finished_at = ? AND id <= ?)) "
                "ORDER BY finished_at DESC, id DESC LIMIT ?",
                (key, *states, at, at, job_id, window),
            ).fetchall()
        ]
        if not ids:
            return [], 0, 0
        id_marks = ",".join("?" * len(ids))
        mine = [
            str(r[0])
            for r in conn.execute(
                "SELECT name FROM job_failures WHERE job_id=? ORDER BY seq", (job_id,)
            ).fetchall()
        ]
        rows: list[FailureRow] = []
        if mine:
            name_marks = ",".join("?" * len(mine))
            counted = {
                r["name"]: (int(r["seen"]), r["first_id"], r["last_id"])
                for r in conn.execute(
                    f"SELECT name, COUNT(*) AS seen, MIN(job_id) AS first_id, "
                    f"MAX(job_id) AS last_id FROM job_failures "
                    f"WHERE job_id IN ({id_marks}) AND name IN ({name_marks}) GROUP BY name",
                    (*ids, *mine),
                ).fetchall()
            }
            for name in mine:  # 잡이 찍은 순서를 지킨다
                seen, first_id, last_id = counted.get(name, (0, None, None))
                rows.append(
                    FailureRow(
                        name=name,
                        seen=seen,
                        first_seen_job_id=int(first_id) if first_id is not None else None,
                        last_seen_job_id=int(last_id) if last_id is not None else None,
                    )
                )
        # 이름을 남긴 잡을 한 번에 받아 파이썬에서 센다 — id 목록을 두 번 바인딩하면
        # `failure_window_jobs` 상한(500)에서 파라미터가 1000개를 넘어 옛 SQLite 가 거절한다.
        named = {
            int(r[0])
            for r in conn.execute(
                f"SELECT DISTINCT job_id FROM job_failures WHERE job_id IN ({id_marks})", ids
            ).fetchall()
        }
        failed_ids = {
            int(r[0])
            for r in conn.execute(
                f"SELECT id FROM jobs WHERE id IN ({id_marks}) AND state IN (?,?)",
                (*ids, FAILED, TIMED_OUT),
            ).fetchall()
        }
        return rows, len(ids), len(failed_ids - named)

    def delete_old_jobs(self, cutoff: datetime) -> int:
        """산출물이 이미 지워진 종료 잡 중 cutoff 전에 끝난 것의 행·이벤트·합류자·제출을 지운다."""
        marks = ",".join("?" * len(TERMINAL_STATES))
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            ids = [
                int(r[0])
                for r in conn.execute(
                    f"SELECT id FROM jobs WHERE state IN ({marks}) "
                    "AND artifacts_purged_at IS NOT NULL "
                    "AND COALESCE(finished_at, created_at) < ? "
                    # M5e: 묶음 파일도 예약도 없어야 지운다 — 삭제에 실패한 번들이 소유 기록과
                    # 회계를 잃으면 안 된다(§8). 번들 행이 아예 없는 옛 잡은 예전 규칙 그대로.
                    "AND id NOT IN (SELECT job_id FROM job_artifacts "
                    "WHERE purged_at IS NULL OR reserved_bytes > 0)",
                    (*sorted(TERMINAL_STATES), _ts(cutoff)),
                ).fetchall()
            ]
            if ids:
                id_marks = ",".join("?" * len(ids))
                conn.execute(f"DELETE FROM events WHERE job_id IN ({id_marks})", ids)
                conn.execute(f"DELETE FROM joiners WHERE job_id IN ({id_marks})", ids)
                # capability 행도 같은 트랜잭션에서(Codex 대안 G5) — 잡 없는 비밀은 남기지 않는다
                conn.execute(f"DELETE FROM submissions WHERE job_id IN ({id_marks})", ids)
                conn.execute(f"DELETE FROM job_artifacts WHERE job_id IN ({id_marks})", ids)
                conn.execute(f"DELETE FROM job_failures WHERE job_id IN ({id_marks})", ids)
                conn.execute(f"DELETE FROM jobs WHERE id IN ({id_marks})", ids)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return len(ids)

    # ── 잡 산출물 묶음 (M5e) ─────────────────────────────────────────────────

    @contextlib.contextmanager
    def hold_bundle(self, job_id: int) -> Iterator[None]:
        """묶음을 내려보내는 동안 잡아 둔다(§6).

        unlink 로는 공간이 안 돌아온다 — 마지막 독자가 닫을 때까지 바이트를 회계에 남긴다.
        잡고 있는 동안 만료가 지나면 청소기가 파일만 지우고 `purged_at` 은 안 찍는다.
        """
        job_id = int(job_id)
        with self._lock:
            self._holds[job_id] = self._holds.get(job_id, 0) + 1
        try:
            yield
        finally:
            with self._lock:
                left = self._holds.get(job_id, 1) - 1
                if left <= 0:
                    self._holds.pop(job_id, None)
                else:
                    self._holds[job_id] = left

    def bundle_is_held(self, job_id: int) -> bool:
        with self._lock:
            return int(job_id) in self._holds

    def set_bundle_state(self, job_id: int, state: str) -> None:
        """상태만 바꾼다 — `purged_at` 은 안 찍는다(아직 독자가 있다)."""
        self._conn().execute(
            "UPDATE job_artifacts SET state=? WHERE job_id=? AND purged_at IS NULL",
            (state, int(job_id)),
        )

    def _artifacts_root(self) -> Path:
        return self.path.parent / "artifacts"

    def get_bundle(self, job_id: int) -> dict[str, Any] | None:
        """묶음 행 하나. 시각은 UTC aware datetime, `reason_args` 는 푼 dict 다(§18)."""
        row = (
            self._conn()
            .execute("SELECT * FROM job_artifacts WHERE job_id=?", (int(job_id),))
            .fetchone()
        )
        if row is None:
            return None
        manifest = json.loads(row["manifest_json"]) if row["manifest_json"] else None
        return {
            "job_id": int(row["job_id"]),
            "state": row["state"],
            "policy": json.loads(row["policy_json"]) if row["policy_json"] else None,
            "files": (manifest or {}).get("files", []),
            "bundle_sha256": row["bundle_sha256"],
            "file_count": row["file_count"],
            "total_bytes": row["total_bytes"],
            "bundle_bytes": row["bundle_bytes"],
            "reserved_bytes": int(row["reserved_bytes"] or 0),
            "skipped_count": row["skipped_count"],
            "collected_at": _dt(row["collected_at"]),
            "ready_at": _dt(row["ready_at"]),
            "expires_at": _dt(row["expires_at"]),
            "acked_at": _dt(row["acked_at"]),
            "purged_at": _dt(row["purged_at"]),
            "reason_code": row["reason_code"],
            "reason_args": outcome.load_args(row["reason_args"]) if row["reason_args"] else None,
            "detail": json.loads(row["detail_json"]) if row["detail_json"] else None,
        }

    def start_collect(self, job_id: int, policy: Any, now: datetime) -> None:
        """수집을 시작했다고 표시하고 **얼린 정책**을 남긴다(§9)."""
        frozen = json.dumps(
            {
                "globs": list(policy.globs),
                "max_bytes": policy.max_bytes,
                "max_files": policy.max_files,
                "timeout_seconds": policy.timeout_seconds,
                "cancel_timeout_seconds": policy.cancel_timeout_seconds,
            },
            separators=(",", ":"),
        )
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "INSERT INTO job_artifacts (job_id, state, policy_json, collected_at) "
                "VALUES (?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET state=excluded.state, "
                "policy_json=excluded.policy_json, collected_at=excluded.collected_at",
                (int(job_id), artifacts.COLLECTING, frozen, _ts(now)),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def bundle_storage_totals(self) -> tuple[int, int]:
        """(발행되어 디스크에 있는 바이트, 예약된 바이트). 지운 묶음은 세지 않는다."""
        row = (
            self._conn()
            .execute(
                "SELECT COALESCE(SUM(CASE WHEN purged_at IS NULL THEN bundle_bytes ELSE 0 END), 0) "
                "AS stored, COALESCE(SUM(reserved_bytes), 0) AS reserved FROM job_artifacts"
            )
            .fetchone()
        )
        return int(row["stored"] or 0), int(row["reserved"] or 0)

    def reserve_bundle_bytes(self, job_id: int, want: int, limit: int, now: datetime) -> bool:
        """전체 상한 안에서 자리를 잡는다. **잡당 예약은 하나**라 다시 잡으면 더하지 않고 바꾼다.

        넘으면 아무것도 바꾸지 않고 False — 만료되지 않은 남의 묶음을 쫓아내지 않는다(§8).
        """
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT COALESCE(SUM(CASE WHEN purged_at IS NULL THEN bundle_bytes ELSE 0 END), 0) "
                "AS stored, COALESCE(SUM(CASE WHEN job_id=? THEN 0 ELSE reserved_bytes END), 0) "
                "AS others FROM job_artifacts",
                (int(job_id),),
            ).fetchone()
            if int(row["stored"] or 0) + int(row["others"] or 0) + int(want) > int(limit):
                conn.execute("ROLLBACK")
                return False
            conn.execute(
                "INSERT INTO job_artifacts (job_id, state, reserved_bytes, collected_at) "
                "VALUES (?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET "
                "reserved_bytes=excluded.reserved_bytes",
                (int(job_id), artifacts.COLLECTING, int(want), _ts(now)),
            )
            conn.execute("COMMIT")
            return True
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def release_bundle_bytes(self, job_id: int) -> None:
        """예약을 반납한다. 두 번 불러도 조용하다."""
        self._conn().execute(
            "UPDATE job_artifacts SET reserved_bytes=0 WHERE job_id=?", (int(job_id),)
        )

    def publish_bundle(
        self, job_id: int, result: Any, *, now: datetime, expires_at: datetime
    ) -> None:
        """모은 묶음을 발행한다. 예약은 실측으로 바뀌고 TTL 시계가 여기서 시작한다(§8)."""
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            _upsert_bundle(conn, int(job_id), result, now=now, expires_at=expires_at)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def set_bundle_failed(
        self,
        job_id: int,
        state: str,
        reason_code: str,
        reason_args: dict[str, Any] | None,
        now: datetime,
    ) -> None:
        """산출물만 실패시킨다 — 잡의 상태·요약은 건드리지 않는다(§3)."""
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "INSERT INTO job_artifacts (job_id, state, reason_code, reason_args, "
                "reserved_bytes, collected_at) VALUES (?,?,?,?,0,?) "
                "ON CONFLICT(job_id) DO UPDATE SET state=excluded.state, "
                "reason_code=excluded.reason_code, reason_args=excluded.reason_args, "
                "reserved_bytes=0, collected_at=excluded.collected_at",
                (int(job_id), state, reason_code, outcome.dump_args(reason_args), _ts(now)),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def ack_bundle(self, job_id: int, sent_sha: str, *, owner: bool, now: datetime) -> Any:
        """확인(ack) 하나를 처리한다. 판정은 순수 규칙이 하고 여기서는 기록만 한다(§7).

        **지울 자격만 준다** — 물리적으로 지우는 것은 청소기다. 동시에 온 마지막 확인 둘 중
        하나에만 `purge` 를 준다(두 번 지우면 회계가 두 번 줄어든다).
        """
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT state, bundle_sha256, expires_at, acked_at FROM job_artifacts "
                "WHERE job_id=?",
                (int(job_id),),
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                return artifacts.AckDecision(409, False, False, "not_ready")
            expiry = _dt(row["expires_at"])
            decision = artifacts.ack_decision(
                state=row["state"],
                join_count=self._join_count(conn, job_id),
                owner=owner,
                stored_sha=row["bundle_sha256"],
                sent_sha=sent_sha,
                expired=expiry is not None and now >= expiry,
            )
            if decision.record and row["acked_at"] is not None:
                # 이미 누가 확인했다 — 두 번 지우라고 하지 않는다.
                decision = artifacts.AckDecision(
                    decision.status, False, False, decision.reason_code
                )
            if decision.record:
                conn.execute(
                    "UPDATE job_artifacts SET acked_at=? WHERE job_id=? AND acked_at IS NULL",
                    (_ts(now), int(job_id)),
                )
            conn.execute("COMMIT")
            return decision
        except Exception:
            conn.execute("ROLLBACK")
            raise

    @staticmethod
    def _join_count(conn: sqlite3.Connection, job_id: int) -> int:
        row = conn.execute("SELECT join_count FROM jobs WHERE id=?", (int(job_id),)).fetchone()
        return int(row["join_count"] or 0) if row is not None else 0

    def set_bundle_counts(self, job_id: int, file_count: int) -> None:
        """워커가 센 파일 수를 적는다 — 서버가 매니페스트를 못 봤을 때만 쓴다."""
        self._conn().execute(
            "UPDATE job_artifacts SET file_count=? WHERE job_id=?", (int(file_count), int(job_id))
        )

    def bundles_due(self, now: datetime, limit: int = 1000) -> list[Any]:
        """TTL 이 지난 묶음. 만료 없는 행과 이미 사라진 것은 절대 대상이 아니다(§8)."""
        marks = ",".join("?" * len(artifacts.GONE_STATES))
        rows = (
            self._conn()
            .execute(
                f"SELECT job_id, state, expires_at, bundle_bytes FROM job_artifacts "
                f"WHERE expires_at IS NOT NULL AND expires_at <= ? AND state NOT IN ({marks}) "
                "ORDER BY job_id LIMIT ?",
                (_ts(now), *sorted(artifacts.GONE_STATES), int(limit)),
            )
            .fetchall()
        )
        return [
            BundleInfo(
                job_id=int(r["job_id"]),
                state=r["state"],
                expires_at=_dt(r["expires_at"]),
                bytes=int(r["bundle_bytes"] or 0),
            )
            for r in rows
        ]

    def mark_bundles_purged(self, job_ids: Iterable[int], state: str, now: datetime) -> int:
        """지웠다고 표시하고 예약을 반납한다. 이미 표시된 것은 다시 세지 않는다."""
        ids = [int(i) for i in job_ids]
        if not ids:
            return 0
        id_marks = ",".join("?" * len(ids))
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            cur = conn.execute(
                f"UPDATE job_artifacts SET state=?, purged_at=?, reserved_bytes=0 "
                f"WHERE job_id IN ({id_marks}) AND purged_at IS NULL",
                (state, _ts(now), *ids),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return int(cur.rowcount)

    def reconcile_bundles_on_start(self, now: datetime) -> tuple[list[int], list[int]]:
        """시작할 때 파일과 DB 를 화해시킨다(§9). (중단으로 닫은 잡, 파일이 없어진 잡).

        파일 설치와 커밋은 한 트랜잭션이 될 수 없다 — 그 사이에 죽으면 고아가 남는다.
        **묶음이 있다고 해서 `lost` 잡이 성공이 되지는 않는다** — 잡 상태는 건드리지 않는다.
        """
        root = self._artifacts_root()
        staging = root / ".staging"
        if staging.is_dir():
            for entry in staging.iterdir():
                shutil.rmtree(entry, ignore_errors=True) if entry.is_dir() else entry.unlink(
                    missing_ok=True
                )
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            interrupted = [
                int(r["job_id"])
                for r in conn.execute(
                    "SELECT job_id FROM job_artifacts WHERE state IN (?,?) ORDER BY job_id",
                    (artifacts.COLLECTING, artifacts.UPLOADING),
                ).fetchall()
            ]
            if interrupted:
                marks = ",".join("?" * len(interrupted))
                conn.execute(
                    f"UPDATE job_artifacts SET state=?, reason_code='interrupted', "
                    f"reserved_bytes=0 WHERE job_id IN ({marks})",
                    (artifacts.FAILED, *interrupted),
                )
            ready = [
                int(r["job_id"])
                for r in conn.execute(
                    "SELECT job_id FROM job_artifacts WHERE state=? ORDER BY job_id",
                    (artifacts.READY,),
                ).fetchall()
            ]
            gone = [j for j in ready if not (root / str(j) / "bundle.tar").is_file()]
            if gone:
                marks = ",".join("?" * len(gone))
                conn.execute(
                    f"UPDATE job_artifacts SET state=? WHERE job_id IN ({marks})",
                    (artifacts.UNAVAILABLE, *gone),
                )
            known = {
                int(r["job_id"])
                for r in conn.execute("SELECT job_id FROM job_artifacts").fetchall()
            }
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        if root.is_dir():  # 행 없는 설치 디렉터리 — 커밋 전에 죽은 자국이다(§9 ④)
            for entry in root.iterdir():
                if entry.is_dir() and entry.name.isdigit() and int(entry.name) not in known:
                    shutil.rmtree(entry, ignore_errors=True)
        return interrupted, gone

    def list_pools(self) -> list[str]:
        """활성 + 종료 잡이 있는 풀 이름. 기본 풀은 잡이 없어도 맨 앞."""
        conn = self._conn()
        names = [r[0] for r in conn.execute("SELECT DISTINCT pool FROM jobs").fetchall()]
        return [DEFAULT_POOL, *sorted(n for n in names if n != DEFAULT_POOL)]

    def list_jobs_by_state(self, states: Iterable[str]) -> list[Job]:
        states = tuple(states)
        marks = ",".join("?" * len(states))
        return self._jobs(f"SELECT * FROM jobs WHERE state IN ({marks}) ORDER BY id", states)

    # ── 잡 쓰기 ─────────────────────────────────────────────────────────────

    def _event(
        self, conn: sqlite3.Connection, job_id: int, kind: str, payload: dict, at: float
    ) -> None:
        conn.execute(
            "INSERT INTO events (job_id, at, kind, payload) VALUES (?, ?, ?, ?)",
            (job_id, at, kind, json.dumps(payload, separators=(",", ":"))),
        )

    def _set_state(
        self, conn: sqlite3.Connection, job_id: int, state: str, at: float, **cols: Any
    ) -> None:
        """상태 전이 + 열 갱신 + 전이 이벤트를 한 번에(호출자가 트랜잭션을 연다)."""
        sets = ["state=?"] + [f"{k}=?" for k in cols]
        conn.execute(
            f"UPDATE jobs SET {', '.join(sets)} WHERE id=?", (state, *cols.values(), job_id)
        )
        self._event(conn, job_id, EVENT_STATE, {"state": state}, at)

    def create_job(
        self,
        *,
        preset: str,
        inputs: dict[str, Any],
        key: str,
        concurrency_group: str | None,
        source: Source,
        requester: Requester,
        timeout_seconds: int,
        join_key: str | None,
        now: datetime,
        state: str = UPLOADING,
        priority: int = 0,
        pool: str = DEFAULT_POOL,
    ) -> Job:
        """새 잡. tree 는 `uploading`, git_ref 는 바로 `queued` 로 만든다."""
        if state not in (UPLOADING, QUEUED):
            raise StoreError(f"new job cannot start in state {state}")
        conn = self._conn()
        src = {
            "mode": source.mode,
            "repo": source.repo,
            "branch": source.branch,
            "base_sha": source.base_sha,
            "dirty": source.dirty,
            "tree_hash": source.tree_hash,
            "bytes": source.bytes,
            "ref": source.ref,
            "sha": source.sha,
        }
        ts = _ts(now)
        conn.execute("BEGIN IMMEDIATE")
        try:
            cur = conn.execute(
                "INSERT INTO jobs (preset, inputs_json, key, concurrency_group, source_json, "
                "requester_name, requester_label, state, created_at, queued_at, tree_hash, sha, "
                "timeout_seconds, join_key, priority, pool) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    preset,
                    json.dumps(inputs, sort_keys=True, separators=(",", ":")),
                    key,
                    concurrency_group or None,
                    json.dumps(src, separators=(",", ":")),
                    requester.name,
                    requester.label,
                    state,
                    ts,
                    ts if state == QUEUED else None,
                    source.tree_hash,
                    source.sha,
                    timeout_seconds,
                    join_key,
                    int(priority),
                    pool,
                ),
            )
            job_id = int(cur.lastrowid)
            self._event(conn, job_id, EVENT_STATE, {"state": state}, ts)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        job = self.get_job(job_id)
        assert job is not None
        return job

    def find_joinable(self, join_key: str) -> Job | None:
        marks = ",".join("?" * len(ACTIVE_STATES))
        rows = self._jobs(
            f"SELECT * FROM jobs WHERE join_key=? AND state IN ({marks}) ORDER BY id LIMIT 1",
            (join_key, *sorted(ACTIVE_STATES)),
        )
        return rows[0] if rows else None

    def join_or_bump(
        self, join_key: str, name: str, label: str, priority: int, now: datetime
    ) -> Job | None:
        """합류 판정 + 합류자 기록 + 우선순위 상향을 **한 트랜잭션**으로(M5).

        요청자 본인이면 합류자를 안 넣는다. 우선순위는 `max(기존, 요청)` 로만 올라간다.
        """
        marks = ",".join("?" * len(ACTIVE_STATES))
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                f"SELECT id, requester_name, priority FROM jobs WHERE join_key=? "
                f"AND state IN ({marks}) ORDER BY id LIMIT 1",
                (join_key, *sorted(ACTIVE_STATES)),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            job_id = int(row["id"])
            if row["requester_name"] != name:
                conn.execute(
                    "INSERT OR IGNORE INTO joiners (job_id, name, label, joined_at) "
                    "VALUES (?, ?, ?, ?)",
                    (job_id, name, label, _ts(now)),
                )
            # 합류자 줄이 안 늘어도 센다 — 요청자 본인 재제출(줄 없음)도, 같은 합류자의 재제출
            # (`INSERT OR IGNORE`)도 「이 잡을 기다리는 세션이 하나 더 생겼다」는 뜻이다(§7).
            conn.execute("UPDATE jobs SET join_count=join_count+1 WHERE id=?", (job_id,))
            if int(priority) > int(row["priority"] or 0):
                conn.execute("UPDATE jobs SET priority=? WHERE id=?", (int(priority), job_id))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return self.get_job(job_id)

    def set_priority(self, job_id: int, priority: int, now: datetime) -> bool:
        """대기 잡(uploading · queued)의 우선순위를 바꾼다. 아니면 False."""
        marks = ",".join("?" * len(WAITING_STATES))
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            cur = conn.execute(
                f"UPDATE jobs SET priority=? WHERE id=? AND state IN ({marks})",
                (int(priority), job_id, *sorted(WAITING_STATES)),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return cur.rowcount == 1

    # ── 스냅샷 캐시 blob (M5) ────────────────────────────────────────────────

    def have_blobs(self, keys: Iterable[str]) -> set[str]:
        """주어진 키 중 표에 있는 것."""
        keys = list(dict.fromkeys(keys))
        if not keys:
            return set()
        conn = self._conn()
        out: set[str] = set()
        for i in range(0, len(keys), 500):
            chunk = keys[i : i + 500]
            marks = ",".join("?" * len(chunk))
            rows = conn.execute(f"SELECT sha256 FROM blobs WHERE sha256 IN ({marks})", chunk)
            out.update(r[0] for r in rows.fetchall())
        return out

    def record_blobs(self, items: Iterable[tuple[str, int]], now: datetime) -> int:
        """받은 blob 을 기록한다(있으면 크기·last_used_at 갱신). 기록한 수."""
        rows = [(k, int(size), _ts(now), _ts(now)) for k, size in items]
        if not rows:
            return 0
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.executemany(
                "INSERT INTO blobs (sha256, size, created_at, last_used_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(sha256) DO UPDATE SET size=excluded.size, "
                "last_used_at=excluded.last_used_at",
                rows,
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return len(rows)

    def touch_blobs(self, keys: Iterable[str], now: datetime) -> int:
        """참조된 blob 의 last_used_at 갱신(GC 가 안 지우게)."""
        keys = list(dict.fromkeys(keys))
        if not keys:
            return 0
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            n = 0
            for i in range(0, len(keys), 500):
                chunk = keys[i : i + 500]
                marks = ",".join("?" * len(chunk))
                n += conn.execute(
                    f"UPDATE blobs SET last_used_at=? WHERE sha256 IN ({marks})",
                    (_ts(now), *chunk),
                ).rowcount
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return n

    def list_blobs(self) -> list[BlobInfo]:
        conn = self._conn()
        return [
            BlobInfo(sha256=r["sha256"], size=int(r["size"]), last_used_at=_dt(r["last_used_at"]))
            for r in conn.execute(
                "SELECT sha256, size, last_used_at FROM blobs ORDER BY last_used_at, sha256"
            ).fetchall()
        ]

    def blob_stats(self) -> tuple[int, int]:
        """(blob 수, 합계 바이트)."""
        row = self._conn().execute("SELECT COUNT(*), COALESCE(SUM(size), 0) FROM blobs").fetchone()
        return int(row[0]), int(row[1])

    def delete_blobs(self, keys: Iterable[str]) -> int:
        keys = list(dict.fromkeys(keys))
        if not keys:
            return 0
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            n = 0
            for i in range(0, len(keys), 500):
                chunk = keys[i : i + 500]
                marks = ",".join("?" * len(chunk))
                n += conn.execute(f"DELETE FROM blobs WHERE sha256 IN ({marks})", chunk).rowcount
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return n

    # ── 알림 전송 기록 (M5) ─────────────────────────────────────────────────

    def claim_notification(self, job_id: int, notify_name: str, now: datetime) -> bool:
        """(잡, 규칙) 행을 unique insert 로 선점한다. 이미 있으면 False — 중복 발송 방지."""
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            cur = conn.execute(
                "INSERT OR IGNORE INTO notifications (job_id, notify_name, claimed_at) "
                "VALUES (?, ?, ?)",
                (job_id, notify_name, _ts(now)),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return cur.rowcount == 1

    def mark_notification(
        self, job_id: int, notify_name: str, *, delivered: bool, now: datetime
    ) -> None:
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                "UPDATE notifications SET delivered_at=?, failed=? "
                "WHERE job_id=? AND notify_name=?",
                (_ts(now) if delivered else None, 0 if delivered else 1, job_id, notify_name),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def list_unnotified_finished(self, since: datetime) -> list[Job]:
        """알림 행이 하나도 없는 종료 잡(since 이후에 끝난 것). 시작 시 스캔용."""
        marks = ",".join("?" * len(TERMINAL_STATES))
        return self._jobs(
            f"SELECT * FROM jobs WHERE state IN ({marks}) AND finished_at >= ? "
            "AND id NOT IN (SELECT job_id FROM notifications) ORDER BY id",
            (*sorted(TERMINAL_STATES), _ts(since)),
        )

    def add_joiner(self, job_id: int, name: str, label: str, now: datetime) -> bool:
        conn = self._conn()
        cur = conn.execute(
            "INSERT OR IGNORE INTO joiners (job_id, name, label, joined_at) VALUES (?, ?, ?, ?)",
            (job_id, name, label, _ts(now)),
        )
        return cur.rowcount == 1

    def remove_joiner(self, job_id: int, name: str) -> bool:
        cur = self._conn().execute("DELETE FROM joiners WHERE job_id=? AND name=?", (job_id, name))
        return cur.rowcount == 1

    # ── 제출 capability (M5j G5 · 결정 87) ──────────────────────────────────

    def add_submission(self, job_id: int, role: str, now: datetime) -> tuple[str, str]:
        """`POST /jobs` 시도 하나에 capability 하나 — `(submission_id, cancel_token)`.

        비밀은 여기서 한 번 만들어 돌려주고 DB 에는 SHA-256 만 남는다(토큰과 같은 취급). 같은
        토큰 이름의 세션이 몇 번을 내든 행은 시도마다 다르다 — 참여자는 이름이 아니라 이 행이다.
        """
        if role not in (ROLE_CANCEL_JOB, ROLE_LEAVE_SUBMISSION):
            raise StoreError(f"unknown submission role {role!r}")
        submission_id = secrets.token_hex(8)
        cancel_token = secrets.token_urlsafe(32)
        self._conn().execute(
            "INSERT INTO submissions (job_id, submission_id, role, capability_hash, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (job_id, submission_id, role, hash_token(cancel_token), _ts(now)),
        )
        return submission_id, cancel_token

    def submission_role(self, job_id: int, cancel_token: str) -> tuple[str, str] | None:
        """그 잡의 어느 capability 와 맞는가 — `(submission_id, role)`, 아니면 None.

        비교는 해시끼리 `compare_digest` 로. 잡 번호가 다르면 비밀이 맞아도 None 이다 — 한 잡의
        비밀로 다른 잡을 건드릴 수 없다.
        """
        want = hash_token(cancel_token)
        rows = (
            self._conn()
            .execute(
                "SELECT submission_id, role, capability_hash FROM submissions WHERE job_id=?",
                (job_id,),
            )
            .fetchall()
        )
        for r in rows:
            if hmac.compare_digest(str(r["capability_hash"]), want):
                return str(r["submission_id"]), str(r["role"])
        return None

    def remove_submission(self, submission_id: str) -> bool:
        """참여자가 나갔다 — 그 비밀은 더 통하지 않는다."""
        cur = self._conn().execute(
            "DELETE FROM submissions WHERE submission_id=?", (submission_id,)
        )
        return cur.rowcount == 1

    def update_source_fields(self, job_id: int, **fields: Any) -> None:
        """source_json 의 키 몇 개를 갱신한다(M5 캐시의 uploaded_bytes · cached_bytes)."""
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT source_json FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is not None:
                src = json.loads(row["source_json"])
                src.update(fields)
                conn.execute(
                    "UPDATE jobs SET source_json=? WHERE id=?",
                    (json.dumps(src, separators=(",", ":")), job_id),
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def update_received(self, job_id: int, received_bytes: int, now: datetime) -> None:
        self._conn().execute(
            "UPDATE jobs SET received_bytes=?, last_received_at=? WHERE id=? AND state=?",
            (received_bytes, _ts(now), job_id, UPLOADING),
        )

    def mark_uploaded(self, job_id: int, total_bytes: int, now: datetime) -> bool:
        """uploading → queued. 이미 취소됐으면 False."""
        conn = self._conn()
        ts = _ts(now)
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT state, source_json FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
            if row is None or row["state"] != UPLOADING:
                conn.execute("ROLLBACK")
                return False
            src = json.loads(row["source_json"])
            src["bytes"] = total_bytes
            conn.execute(
                "UPDATE jobs SET source_json=?, received_bytes=?, last_received_at=? WHERE id=?",
                (json.dumps(src, separators=(",", ":")), total_bytes, ts, job_id),
            )
            self._set_state(conn, job_id, QUEUED, ts, queued_at=ts)
            conn.execute("COMMIT")
            return True
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def claim(
        self,
        lane: int,
        now: datetime,
        pool: str = DEFAULT_POOL,
        worker_name: str | None = None,
    ) -> Job | None:
        """그 풀의 queued 잡 하나를 원자적으로 running 으로. 그룹 배제는 **풀 안에서**(M5b).

        `worker_name` 이 있으면(원격 워커) 같은 트랜잭션에서 그 워커의 그 레인에 활성 잡이
        없는지 확인한다 — 있으면 `LaneBusy`(M5b-2, 레인 과할당 금지). 로컬 레인은 None.
        """
        conn = self._conn()
        ts = _ts(now)
        conn.execute("BEGIN IMMEDIATE")
        try:
            busy = ",".join("?" * len(BUSY_STATES))
            if worker_name is not None:
                taken = conn.execute(
                    f"SELECT id FROM jobs WHERE worker_name=? AND lane=? AND state IN ({busy}) "
                    "ORDER BY id LIMIT 1",
                    (worker_name, lane, *sorted(BUSY_STATES)),
                ).fetchone()
                if taken is not None:
                    conn.execute("ROLLBACK")
                    raise LaneBusy(f"lane {lane} already has job #{int(taken['id'])}")
            row = conn.execute(
                "SELECT id FROM jobs WHERE state=? AND pool=? AND (concurrency_group IS NULL OR "
                "concurrency_group NOT IN (SELECT concurrency_group FROM jobs "
                f"WHERE state IN ({busy}) AND pool=? "
                "AND concurrency_group IS NOT NULL)) ORDER BY priority DESC, id LIMIT 1",
                (QUEUED, pool, *sorted(BUSY_STATES), pool),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            job_id = int(row["id"])
            # 같은 트랜잭션에서 센다 — 나중에는 알 수 없는 값이다(자기 포함).
            busy_now = conn.execute(
                f"SELECT count(*) AS n FROM jobs WHERE state IN ({busy}) AND pool=?",
                (*sorted(BUSY_STATES), pool),
            ).fetchone()["n"]
            cur = conn.execute(
                "UPDATE jobs SET state=?, lane=?, started_at=?, phase=?, last_output_at=?, "
                "worker_name=?, concurrent_at_start=? WHERE id=? AND state=?",
                (
                    RUNNING,
                    lane,
                    ts,
                    PHASE_MATERIALIZING,
                    ts,
                    worker_name,
                    int(busy_now) + 1,
                    job_id,
                    QUEUED,
                ),
            )
            if cur.rowcount != 1:
                conn.execute("ROLLBACK")
                return None
            self._event(conn, job_id, EVENT_STATE, {"state": RUNNING}, ts)
            conn.execute("COMMIT")
        except LaneBusy:
            raise
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return self.get_job(job_id)

    def concurrent_at_start(self, job_id: int) -> int | None:
        """그 잡이 시작할 때 같은 풀에서 돌던 잡 수(자기 포함). **모르면 None** — 0 이 아니다.

        마이그레이션 이전 잡은 NULL 이고, 그건 「혼자 돌았다」가 아니라 「모른다」다.
        """
        row = (
            self._conn()
            .execute("SELECT concurrent_at_start FROM jobs WHERE id=?", (job_id,))
            .fetchone()
        )
        if row is None or row["concurrent_at_start"] is None:
            return None
        return int(row["concurrent_at_start"])

    def set_phase(self, job_id: int, phase: str) -> None:
        self._conn().execute("UPDATE jobs SET phase=? WHERE id=?", (phase, job_id))

    def set_last_output(self, job_id: int, now: datetime) -> None:
        self._conn().execute("UPDATE jobs SET last_output_at=? WHERE id=?", (_ts(now), job_id))

    def request_cancel(
        self, job_id: int, by: str, now: datetime, grace_seconds: float
    ) -> str | None:
        """취소 요청. 대기 잡은 즉시 cancelled, 실행 중이면 cancelling. 종료 잡이면 None."""
        conn = self._conn()
        ts = _ts(now)
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                return None
            state = row["state"]
            if state in (UPLOADING, QUEUED):
                self._set_state(
                    conn,
                    job_id,
                    CANCELLED,
                    ts,
                    finished_at=ts,
                    cancelled_by=by,
                    **_outcome("cancelled_before_start"),
                )
                new = CANCELLED
            elif state == RUNNING:
                kill_at = ts + grace_seconds
                self._set_state(
                    conn,
                    job_id,
                    CANCELLING,
                    ts,
                    cancel_requested_at=ts,
                    cancel_by=by,
                    cancel_kill_at=kill_at,
                )
                new = CANCELLING
            elif state == CANCELLING:
                new = CANCELLING
            else:
                new = None
            conn.execute("COMMIT")
            return new
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def finish(
        self,
        job_id: int,
        state: str,
        *,
        now: datetime,
        exit_code: int | None = None,
        summary: str | None = None,
        summary_code: str | None = None,
        summary_args: dict[str, Any] | None = None,
        failed_step: str | None = None,
        last_step: str | None = None,
        fail_names: Sequence[str] = (),
        fail_truncated: bool = False,
        cancelled_by: str | None = None,
        only_from: Iterable[str] | None = None,
        bundle: Any | None = None,
        ttl_hours: int = 24,
    ) -> bool:
        """종료 상태로. `only_from` 을 주면 그 상태에서만 바뀐다(경쟁 방지).

        `bundle`(`collect.CollectResult`)을 주면 산출물 묶음을 **같은 트랜잭션**에 쓴다 — 거절된
        finish 는 묶음도 안 남긴다(§5). 잡의 요약은 건드리지 않는다: 「테스트가 깨졌다」와
        「산출물을 못 올렸다」는 다른 사실이다(§3).
        """
        if state not in TERMINAL_STATES:
            raise StoreError(f"{state} is not a terminal state")
        conn = self._conn()
        ts = _ts(now)
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT state, cancel_by FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None or row["state"] in TERMINAL_STATES:
                conn.execute("ROLLBACK")
                return False
            if only_from is not None and row["state"] not in set(only_from):
                conn.execute("ROLLBACK")
                return False
            self._set_state(
                conn,
                job_id,
                state,
                ts,
                finished_at=ts,
                exit_code=exit_code,
                summary=summary,
                summary_code=summary_code,
                summary_args=dump_args(summary_args),
                failed_step=failed_step,
                last_step=last_step,
                fail_truncated=1 if fail_truncated else 0,
                cancelled_by=cancelled_by if cancelled_by is not None else row["cancel_by"],
                lane=None,
                phase=None,
            )
            # 증거와 결과는 **같은 커밋**이다 — 거절된 finish 는 대장도 안 남긴다.
            for seq, name in enumerate(fail_names, start=1):
                conn.execute(
                    "INSERT OR IGNORE INTO job_failures(job_id, name, seq) VALUES (?,?,?)",
                    (job_id, name, seq),
                )
            if bundle is not None:
                _upsert_bundle(conn, job_id, bundle, now=now, ttl_hours=ttl_hours)
            conn.execute("COMMIT")
            return True
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def recover_on_start(self, now: datetime) -> tuple[list[int], list[int]]:
        """서버 시작 정리. 로컬 레인의 running·cancelling → lost, uploading → cancelled.

        원격 워커(`worker_name`)의 잡은 건드리지 않는다 — 워커는 살아 있을 수 있고, 아니면
        `mark_lost_for_worker` 가 heartbeat 시각으로 닫는다(M5b-2).
        """
        conn = self._conn()
        ts = _ts(now)
        when = now.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%SZ")
        recover_state = LOST  # mutcheck: restart-lost — 조용히 succeeded/queued 로 되돌리지 않는다
        lost: list[int] = []
        cancelled: list[int] = []
        conn.execute("BEGIN IMMEDIATE")
        try:
            for row in conn.execute(
                "SELECT id FROM jobs WHERE state IN (?, ?) AND worker_name IS NULL ORDER BY id",
                (RUNNING, CANCELLING),
            ).fetchall():
                self._set_state(
                    conn,
                    row["id"],
                    recover_state,
                    ts,
                    finished_at=ts,
                    **_outcome("server_restarted", at=when),
                    lane=None,
                    phase=None,
                )
                lost.append(int(row["id"]))
            for row in conn.execute(
                "SELECT id FROM jobs WHERE state=? ORDER BY id", (UPLOADING,)
            ).fetchall():
                self._set_state(
                    conn,
                    row["id"],
                    CANCELLED,
                    ts,
                    finished_at=ts,
                    **_outcome("server_restarted_during_upload"),
                    cancelled_by="server",
                )
                cancelled.append(int(row["id"]))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return lost, cancelled

    # ── 이벤트 ──────────────────────────────────────────────────────────────

    def add_marker(self, job_id: int, kind: str, value: str, at: datetime) -> None:
        """마커 하나. 로컬 워커의 펌프는 한 줄씩 흘리므로 묶을 것이 없다(`worker.py`)."""
        self._event(self._conn(), job_id, EVENT_MARKER, {"kind": kind, "value": value}, _ts(at))

    def add_markers(self, job_id: int, items: Sequence[tuple[str, str]], at: datetime) -> None:
        """마커 여럿을 **트랜잭션 하나**로. 원격 워커의 로그 flush 는 한 번에 수천 줄이 온다.

        줄마다 트랜잭션을 열면(옛 동작) 다른 레인의 `claim` 이 밀린다 — 256 KB flush 하나에
        0.03 ms → 275.9 ms, 4 MB 본문이면 `busy_timeout` 이 터진다. 빈 목록은 트랜잭션을 열지
        않고, 중간에 실패하면 통째로 롤백한다(부분 마커를 남기지 않는다).
        """
        if not items:
            return
        ts = _ts(at)
        rows = [
            (job_id, ts, EVENT_MARKER, json.dumps({"kind": k, "value": v}, separators=(",", ":")))
            for k, v in items
        ]
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.executemany(
                "INSERT INTO events (job_id, at, kind, payload) VALUES (?, ?, ?, ?)", rows
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def markers(self, job_id: int) -> list[Marker]:
        out: list[Marker] = []
        for e in self._conn().execute(
            "SELECT at, payload FROM events WHERE job_id=? AND kind=? ORDER BY id",
            (job_id, EVENT_MARKER),
        ):
            p = json.loads(e["payload"])
            out.append(Marker(at=_dt(e["at"]), kind=p["kind"], value=p["value"]))
        return out

    def markers_for(self, job_ids: Iterable[int]) -> dict[int, list[Marker]]:
        ids = tuple(job_ids)
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        out: dict[int, list[Marker]] = {i: [] for i in ids}
        for e in self._conn().execute(
            "SELECT job_id, at, payload FROM events "
            f"WHERE kind=? AND job_id IN ({marks}) ORDER BY id",
            (EVENT_MARKER, *ids),
        ):
            p = json.loads(e["payload"])
            out[e["job_id"]].append(Marker(at=_dt(e["at"]), kind=p["kind"], value=p["value"]))
        return out

    # ── 토큰 ────────────────────────────────────────────────────────────────

    def add_token(
        self, name: str, *, admin: bool = False, now: datetime, kind: str | None = None
    ) -> str:
        """무작위 32바이트 토큰을 만들어 **한 번만** 돌려준다. DB 에는 sha256 만 남는다.

        `kind` 가 없으면 `admin` 으로 정한다(client|admin). `admin` 열은 `kind == "admin"` 과
        항상 같게 둔다(옛 코드 경로 호환).
        """
        if kind is None:
            kind = TOKEN_ADMIN if admin else TOKEN_CLIENT
        if kind not in TOKEN_KINDS:
            raise ValueError(f"unknown token kind {kind!r}")
        secret = secrets.token_urlsafe(32)
        try:
            self._conn().execute(
                "INSERT INTO tokens (name, sha256, admin, created_at, kind) VALUES (?, ?, ?, ?, ?)",
                (name, hash_token(secret), 1 if kind == TOKEN_ADMIN else 0, _ts(now), kind),
            )
        except sqlite3.IntegrityError as e:
            raise StoreError(f"token '{name}' already exists") from e
        return secret

    def verify_token(self, secret: str | None) -> TokenInfo | None:
        """제시된 토큰이 유효(폐기되지 않음)하면 TokenInfo. 비교는 compare_digest."""
        if not secret:
            return None
        digest = hash_token(secret)
        for row in self._conn().execute(
            "SELECT name, sha256, admin, created_at, kind FROM tokens WHERE revoked_at IS NULL"
        ):
            if hmac.compare_digest(row["sha256"], digest):
                return TokenInfo(
                    name=row["name"],
                    admin=bool(row["admin"]),
                    created_at=_dt(row["created_at"]),
                    kind=row["kind"] or TOKEN_CLIENT,
                )
        return None

    def list_tokens(self) -> list[TokenInfo]:
        return [
            TokenInfo(
                name=r["name"],
                admin=bool(r["admin"]),
                created_at=_dt(r["created_at"]),
                revoked_at=_dt(r["revoked_at"]),
                kind=r["kind"] or TOKEN_CLIENT,
            )
            for r in self._conn().execute(
                "SELECT name, admin, created_at, revoked_at, kind FROM tokens "
                "ORDER BY created_at, name"
            )
        ]

    def revoke_token(self, name: str, now: datetime) -> bool:
        cur = self._conn().execute(
            "UPDATE tokens SET revoked_at=? WHERE name=? AND revoked_at IS NULL", (_ts(now), name)
        )
        return cur.rowcount == 1

    # ── 원격 워커 (M5b-2) ───────────────────────────────────────────────────

    @staticmethod
    def _row_to_worker(row: sqlite3.Row) -> WorkerRow:
        return WorkerRow(
            name=row["name"],
            pool=row["pool"],
            lanes=int(row["lanes"]),
            host_name=row["host_name"],
            version=row["version"],
            registered_at=_dt(row["registered_at"]),
            last_seen_at=_dt(row["last_seen_at"]),
        )

    def register_worker(
        self,
        name: str,
        *,
        pool: str,
        lanes: int,
        host_name: str | None,
        version: str | None,
        now: datetime,
    ) -> WorkerRow:
        """등록/갱신(upsert). `last_seen_at = now`. 처음 등록 시각은 유지한다."""
        ts = _ts(now)
        self._conn().execute(
            "INSERT INTO workers (name, pool, lanes, host_name, version, registered_at, "
            "last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(name) DO UPDATE SET "
            "pool=excluded.pool, lanes=excluded.lanes, host_name=excluded.host_name, "
            "version=excluded.version, last_seen_at=excluded.last_seen_at",
            (name, pool, int(lanes), host_name, version, ts, ts),
        )
        row = self.get_worker(name)
        assert row is not None
        return row

    def get_worker(self, name: str) -> WorkerRow | None:
        row = self._conn().execute("SELECT * FROM workers WHERE name=?", (name,)).fetchone()
        return self._row_to_worker(row) if row else None

    def list_workers(self) -> list[WorkerRow]:
        return [
            self._row_to_worker(r)
            for r in self._conn().execute("SELECT * FROM workers ORDER BY name").fetchall()
        ]

    def forget_workers(self, cutoff: datetime) -> list[str]:
        """`cutoff` 이전에 마지막으로 보인 워커를 잊는다. 지운 이름을 돌려준다.

        지우는 코드가 없어서 은퇴한 워커가 영원히 남았다 — `server.workers[]` 에 `down` 레인이
        계속 쌓이고 매 요청에 실린다. **활성 잡이 있는 워커는 아무리 오래됐어도 안 지운다**:
        그 잡이 큐에서 사라지면 안 된다(M5f 결정 49).
        """
        busy = ",".join("?" * len(BUSY_STATES))
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            waiting = ",".join("?" * len(WAITING_STATES))
            rows = conn.execute(
                f"SELECT name FROM workers WHERE last_seen_at < ? AND name NOT IN "
                f"(SELECT worker_name FROM jobs WHERE worker_name IS NOT NULL "
                f"AND state IN ({busy})) "
                # 그 풀에 아직 기다리는 잡이 있으면 「은퇴」가 아니라 「일주일째 고장」이다.
                # 지우면 `pools_without_workers` 가 비어 `rcm check` 의 경보가 꺼진다 —
                # 잡은 그대로 멈춰 있는데.
                f"AND pool NOT IN (SELECT DISTINCT pool FROM jobs WHERE state IN ({waiting})) "
                "ORDER BY name",
                (_ts(cutoff), *sorted(BUSY_STATES), *sorted(WAITING_STATES)),
            ).fetchall()
            gone = [r["name"] for r in rows]
            if gone:
                marks = ",".join("?" * len(gone))
                conn.execute(f"DELETE FROM workers WHERE name IN ({marks})", tuple(gone))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return gone

    def touch_worker(self, name: str, now: datetime) -> bool:
        """heartbeat — `last_seen_at` 은 **서버 시각**으로만 쓴다. 모르는 워커면 False."""
        cur = self._conn().execute(
            "UPDATE workers SET last_seen_at=? WHERE name=?", (_ts(now), name)
        )
        return cur.rowcount == 1

    def jobs_of_worker(self, name: str) -> list[Job]:
        """그 워커가 claim 해서 아직 running·cancelling 인 잡(레인 순)."""
        busy = ",".join("?" * len(BUSY_STATES))
        return self._jobs(
            f"SELECT * FROM jobs WHERE worker_name=? AND state IN ({busy}) ORDER BY lane, id",
            (name, *sorted(BUSY_STATES)),
        )

    def mark_lost_for_worker(
        self,
        name: str,
        now: datetime,
        summary: str,
        *,
        summary_code: str | None = None,
        summary_args: dict[str, Any] | None = None,
    ) -> list[int]:
        """그 워커의 running·cancelling 잡을 전부 lost 로. 닫은 잡 id 목록."""
        conn = self._conn()
        ts = _ts(now)
        busy = ",".join("?" * len(BUSY_STATES))
        lost: list[int] = []
        conn.execute("BEGIN IMMEDIATE")
        try:
            for row in conn.execute(
                f"SELECT id FROM jobs WHERE worker_name=? AND state IN ({busy}) ORDER BY id",
                (name, *sorted(BUSY_STATES)),
            ).fetchall():
                self._set_state(
                    conn,
                    row["id"],
                    LOST,
                    ts,
                    finished_at=ts,
                    summary=summary[:200],
                    summary_code=summary_code,
                    summary_args=dump_args(summary_args),
                    lane=None,
                    phase=None,
                )
                lost.append(int(row["id"]))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return lost

    # ── 서버 상태 ────────────────────────────────────────────────────────────

    def set_paused(self, by: str, now: datetime) -> None:
        self._conn().execute(
            "INSERT OR REPLACE INTO server_state (key, value) VALUES ('paused', ?)",
            (json.dumps({"by": by, "at": _ts(now)}),),
        )

    def clear_paused(self) -> None:
        self._conn().execute("DELETE FROM server_state WHERE key='paused'")

    def get_paused(self) -> Paused | None:
        row = self._conn().execute("SELECT value FROM server_state WHERE key='paused'").fetchone()
        if row is None:
            return None
        v = json.loads(row["value"])
        return Paused(by=v["by"], at=_dt(v["at"]))

    # ── 정리 ────────────────────────────────────────────────────────────────

    def abandon_stale_uploads(self, now: datetime, abandon_seconds: float) -> list[int]:
        """`upload_abandon_seconds` 동안 바이트가 안 온 uploading 잡을 cancelled 로 남긴다."""
        conn = self._conn()
        cutoff = _ts(now - timedelta(seconds=abandon_seconds))
        out: list[int] = []
        for row in conn.execute(
            "SELECT id FROM jobs WHERE state=? AND COALESCE(last_received_at, created_at) < ?",
            (UPLOADING, cutoff),
        ).fetchall():
            text, code, args = outcome.summary("upload_abandoned", seconds=int(abandon_seconds))
            if self.finish(
                int(row["id"]),
                CANCELLED,
                now=now,
                summary=text,
                summary_code=code,
                summary_args=args,
                cancelled_by="server",
                only_from=(UPLOADING,),
            ):
                out.append(int(row["id"]))
        return out


__all__ = ["Store", "StoreError", "TokenInfo", "hash_token", "DB_VERSION"]
