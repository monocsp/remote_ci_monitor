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
from remote_ci_monitor.core import shelf as shelf_rules
from remote_ci_monitor.core.capability import CheckRecord
from remote_ci_monitor.core.failures import FailureRow
from remote_ci_monitor.core.model import (
    ACTIVE_STATES,
    BUSY_STATES,
    CANCELLED,
    CANCELLING,
    CHAIN_LINK_STATES,
    DEFAULT_POOL,
    EXPIRED,
    FAILED,
    LOST,
    PHASE_MATERIALIZING,
    PLACEMENT_CAPABLE,
    PLACEMENT_POOL,
    QUEUED,
    RETRIED,
    RUNNING,
    SCHEDULED,
    SCHEDULED_STATES,
    SCOPE_GLOBAL,
    SCOPE_HOST,
    SCOPE_POOL,
    SUCCEEDED,
    TERMINAL_STATES,
    TIMED_OUT,
    TOKEN_ADMIN,
    TOKEN_CLIENT,
    TOKEN_KINDS,
    UPLOADING,
    WAITING_STATES,
    Action,
    CancelInfo,
    Job,
    JobLink,
    Joiner,
    Paused,
    Requester,
    RetryPolicy,
    ShelfItem,
    Source,
    Transition,
)
from remote_ci_monitor.core.outcome import dump_args, load_args
from remote_ci_monitor.core.progress import KIND_ACTION, Marker, action_of, chain_action
from remote_ci_monitor.core.retention import BlobInfo, BundleInfo
from remote_ci_monitor.core.supersede import SUPERSEDED, branch_of, repo_of, superseded_by

#: 실패 대장 창(`failure_stats`)에서 빼는 요약 코드 — 프로세스가 뜨기 **전**에 서버가 닫은 잡
#: (M5j G4 · `tool_missing`). 취소·유실처럼 스크립트에 대해 아무 말도 못 한 잡이다.
WINDOW_EXCLUDED_CODES: tuple[str, ...] = ("tool_missing",)

DB_VERSION = 26
#: 제출 capability 의 역할(M5j G5 · 결정 87). 요청자는 잡을 취소하고, 합류자는 자기 참여만 뺀다.
ROLE_CANCEL_JOB = "cancel_job"
ROLE_LEAVE_SUBMISSION = "leave_submission"
#: 마이그레이션 전 자동 백업을 몇 개 남기나(결정 74). 정리는 마이그레이션이 끝난 뒤, 실패는 경고만.
BACKUPS_KEPT = 3
#: 스토어 버전 드래프트(v20 `versions`)의 상태. closed 둘은 목록의 «지난 것» 이고 다시 열리지
#: 않는다; `running` 은 심사 잡이나 드라이버 회차가 이 버전으로 도는 중이다.
VERSION_CREATING = "creating"
VERSION_EDITING = "editing"
VERSION_RUNNING = "running"
VERSION_SUBMITTED = "submitted"
VERSION_DISCARDED = "discarded"
VERSION_FAILED = "failed"
VERSION_STATES = (
    VERSION_CREATING,
    VERSION_EDITING,
    VERSION_RUNNING,
    VERSION_SUBMITTED,
    VERSION_DISCARDED,
    VERSION_FAILED,
)
VERSION_CLOSED_STATES = (VERSION_SUBMITTED, VERSION_DISCARDED)
#: 손도 안 댄 채 만료되면 청소기가 버리는 상태(워크플랜 Q1 · §14-3). `failed` 는 만들기가 실패한
#: 행이다 — 스토어에는 아무것도 없고(asc id 없음) 이름만 붙잡고 있으므로 같이 치운다.
VERSION_SWEEPABLE_STATES = (VERSION_EDITING, VERSION_FAILED)
#: `update_version` 이 받는 열 — 이름 · 만든 사람 · 만든 시각은 바꾸지 않는다.
VERSION_UPDATABLE = frozenset(
    {
        "state",
        "last_edit_at",
        "expires_at",
        "asc_version_id",
        "prefill_json",
        "edited_json",
        "error",
        "create_job_id",
        "delete_job_id",
        "release_id",
        "review_job_id",
        "upload_job_id",
        "expiry_warned",
    }
)
EVENT_STATE = "state"
EVENT_MARKER = "marker"
#: 그룹 홀드 만들기 · 풀기 사건(M5t-b 결정 330). events 의 스키마는 그대로다
EVENT_HOLD = "hold"
#: 유예 · 취소로 닫힌 원격 잡에 같은 세션의 finish 가 늦게 왔다(M5t-b 결정 256 · 274). 잡 상태는
#: 바꾸지 않고 이 사건 한 건만 남긴다 — events 의 스키마는 그대로다
EVENT_LATE_RESULT = "late_result"
#: 홀드를 푸는 쪽(M5t-b §4.9). `worker` 는 인증된 워커의 확인, `admin` 은 `rcm release-hold`
RELEASE_WORKER = "worker"
RELEASE_ADMIN = "admin"
#: 그룹 없는 표식 프리셋의 합성 그룹 접두어(M5t-b 결정 330 · 5차 (a))
SYNTHETIC_GROUP_PREFIX = "irreversible:"
#: 정지 행 — `get_paused` 와 claim 트랜잭션이 같은 문장으로 본다
_PAUSED_SQL = "SELECT value FROM server_state WHERE key='paused'"

#: v24(M5p · docs/m5p-workplan.md §5.1 · §15.3) — 보관함 항목. 잡 행보다 오래 살아서 외래 키를
#: 걸지 않고 페이지에 필요한 잡의 열을 복사해 든다. 새 DB(`_SCHEMA_V1`)와 마이그레이션이 같은
#: 문장을 쓴다 — `sqlite_master` 의 글자까지 같아야 두 DB 가 같다. `IF NOT EXISTS` 인 이유: 옛
#: 버전으로 깎는 시험 도우미들(과 번호가 밀린 옛 빌드)은 표를 남긴 채 버전만 내린다.
_SHELF_ITEMS_SQL = (
    "CREATE TABLE IF NOT EXISTS shelf_items (job_id INTEGER PRIMARY KEY, state TEXT NOT NULL, "
    "preset TEXT NOT NULL, inputs_json TEXT, repo TEXT, branch TEXT, sha TEXT, source_mode TEXT, "
    "requester TEXT, job_state TEXT, job_finished_at REAL, requested_at REAL NOT NULL, "
    "shelved_at REAL, last_used_at REAL, stale_since REAL, deleted_at REAL, file_count INTEGER, "
    "total_bytes INTEGER, reason_code TEXT, reason_args TEXT, error_code TEXT, failed_at REAL)"
)
_SHELF_STATE_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS shelf_items_state ON shelf_items(state, last_used_at)"
)
_SHELF_LATEST_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS shelf_items_latest ON shelf_items(preset, branch, shelved_at)"
)
#: `shelf_items` 의 열, 표의 차례 그대로 — `ShelfItem` 필드와 같다.
SHELF_COLUMNS: tuple[str, ...] = (
    "job_id",
    "state",
    "preset",
    "inputs_json",
    "repo",
    "branch",
    "sha",
    "source_mode",
    "requester",
    "job_state",
    "job_finished_at",
    "requested_at",
    "shelved_at",
    "last_used_at",
    "stale_since",
    "deleted_at",
    "file_count",
    "total_bytes",
    "reason_code",
    "reason_args",
    "error_code",
    "failed_at",
)

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
  fail_truncated INTEGER NOT NULL DEFAULT 0,
  -- v22: 호출자가 붙인 링크(예: PR). 옛 잡은 NULL. 마이그레이션이 뒤에 붙이므로 여기서도 맨 끝이다
  link_url TEXT,
  link_label TEXT,
  -- v23(M5n): 어디서 돌 수 있나와 어디서 돌았나. 마이그레이션과 같은 차례로 맨 끝이다
  placement TEXT NOT NULL DEFAULT 'pool',
  display_pool TEXT,
  target_worker TEXT,
  concurrency_scope TEXT NOT NULL DEFAULT 'pool',
  host_key TEXT,
  fingerprint_hash TEXT,
  sample INTEGER NOT NULL DEFAULT 1,
  -- v24(M5p): 끝나면 보관함에 넣나. 제출 때 얼린다. 마이그레이션이 뒤에 붙이므로 여기서도 맨 끝이다
  keep INTEGER NOT NULL DEFAULT 0,
  -- v25(M5s): 예약(승격 시각 · 시작 마감) · 얼린 supersede 끄기 · 반복 사슬과 얼린 반복 정책.
  -- 마이그레이션과 같은 차례로 맨 끝이다
  not_before REAL,
  start_deadline REAL,
  no_supersede INTEGER NOT NULL DEFAULT 0,
  chain_id INTEGER,
  retry_of INTEGER,
  attempt INTEGER NOT NULL DEFAULT 1,
  retry_on_exit INTEGER,
  retry_interval_seconds INTEGER,
  retry_max_attempts INTEGER,
  retry_disabled INTEGER NOT NULL DEFAULT 0,
  -- v26(M5t-b §4.9): claim 한 워커 세션 · claim 때 얼린 표식과 유예 · 워커의 claim 순번 · 로그
  -- 오프셋(PR2 가 쓴다). 마이그레이션과 같은 차례로 맨 끝이다. 옛 행은 NULL = 로컬 또는 레거시
  worker_session_id TEXT,
  irreversible INTEGER,
  disconnect_grace_seconds INTEGER,
  claim_seq INTEGER,
  log_offset INTEGER,
  log_file_bytes INTEGER,
  log_complete INTEGER
);
CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state, id);
CREATE INDEX IF NOT EXISTS jobs_worker ON jobs(worker_name, state);
CREATE INDEX IF NOT EXISTS jobs_pool ON jobs(pool);
CREATE INDEX IF NOT EXISTS jobs_claim ON jobs(state, pool, priority DESC, id);
CREATE INDEX IF NOT EXISTS jobs_recent ON jobs(state, finished_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS jobs_join ON jobs(join_key, state);
CREATE INDEX IF NOT EXISTS jobs_finished ON jobs(finished_at);
CREATE INDEX IF NOT EXISTS jobs_key_finished ON jobs(key, finished_at DESC);
CREATE INDEX IF NOT EXISTS jobs_chain ON jobs(chain_id);
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
  created_at REAL NOT NULL,
  participant TEXT NOT NULL
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
CREATE TABLE IF NOT EXISTS releases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repo TEXT NOT NULL,
  build_name TEXT NOT NULL,
  kind TEXT NOT NULL,
  started_by TEXT NOT NULL,
  started_at REAL NOT NULL,
  pid INTEGER,
  log_path TEXT NOT NULL,
  exit_code INTEGER,
  finished_at REAL,
  confirmed_n INTEGER,
  confirmed_by TEXT,
  android_track TEXT,
  dry_run INTEGER NOT NULL DEFAULT 0,
  token_name TEXT,
  auto_n INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS releases_repo ON releases(repo, id DESC);
CREATE TABLE IF NOT EXISTS versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repo TEXT NOT NULL,
  ios_version TEXT,
  android_version TEXT,
  state TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_at REAL NOT NULL,
  last_edit_at REAL,
  expires_at REAL NOT NULL,
  asc_version_id TEXT,
  prefill_json TEXT,
  edited_json TEXT,
  error TEXT,
  create_job_id INTEGER,
  delete_job_id INTEGER,
  release_id INTEGER,
  review_job_id INTEGER,
  expiry_warned INTEGER NOT NULL DEFAULT 0,
  -- v21: `mode=upload` 잡 — 올리는 동안 행은 `running` 이다(마이그레이션이 뒤에 붙이므로 여기서도
  -- 맨 끝이다. 새 DB 와 올라온 DB 의 열 차례가 같아야 한다)
  upload_job_id INTEGER
);
CREATE INDEX IF NOT EXISTS versions_repo ON versions(repo, id DESC);
CREATE TABLE IF NOT EXISTS machines (
  worker TEXT PRIMARY KEY,
  host_key TEXT,
  display_name TEXT,
  accept_any INTEGER NOT NULL DEFAULT 1,
  fingerprint_hash TEXT,
  fingerprint_json TEXT,
  previous_json TEXT,
  changed_at REAL,
  measured_at REAL,
  generation TEXT,
  report_json TEXT
);
CREATE TABLE IF NOT EXISTS fingerprints (
  hash TEXT PRIMARY KEY,
  json TEXT NOT NULL,
  first_seen_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS machine_checks (
  worker TEXT NOT NULL,
  preset TEXT NOT NULL,
  fingerprint_hash TEXT NOT NULL,
  job_id INTEGER,
  submitted_at REAL NOT NULL,
  auto INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS machine_checks_key ON machine_checks(worker, preset, fingerprint_hash);
CREATE INDEX IF NOT EXISTS jobs_check ON jobs(worker_name, preset, fingerprint_hash, id);
"""
_SCHEMA_V1 += f"{_SHELF_ITEMS_SQL};\n{_SHELF_STATE_INDEX_SQL};\n{_SHELF_LATEST_INDEX_SQL};\n"

#: v26(M5t-b §4.9) — 워커 프로세스 한 번 = 세션 하나(결정 265). 현재 세션은 그 이름에서
#: `fenced_at IS NULL` 인 행이고 등록 트랜잭션이 많아야 하나로 지킨다.
_WORKER_SESSIONS_SQL = (
    "CREATE TABLE IF NOT EXISTS worker_sessions (id TEXT PRIMARY KEY, worker_name TEXT NOT NULL, "
    "registered_at REAL NOT NULL, fenced_at REAL, fenced_by TEXT, "
    "orphan_unverified INTEGER NOT NULL DEFAULT 0)"
)
_WORKER_SESSIONS_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS worker_sessions_name ON worker_sessions(worker_name, fenced_at)"
)
#: v26(M5t-b 결정 330 · 339-d) — 표식 잡이 확인 없이 닫히면 그 그룹을 잡아 둔다. 잡 하나에 행
#: 하나이고 풀어도 지우지 않는다(감사 이력). 그룹 · 범위 · 풀 · 호스트는 닫는 트랜잭션에서 잡 행을
#: 복사한다 — 지금 설정은 읽지 않는다.
_IRREVERSIBLE_HOLDS_SQL = (
    "CREATE TABLE IF NOT EXISTS irreversible_holds (job_id INTEGER PRIMARY KEY, "
    "concurrency_group TEXT NOT NULL, concurrency_scope TEXT NOT NULL, pool TEXT NOT NULL, "
    "host_key TEXT, worker_name TEXT, cause TEXT NOT NULL, created_at REAL NOT NULL, "
    "released_at REAL, release_kind TEXT, released_by TEXT, release_reason TEXT)"
)
_IRREVERSIBLE_HOLDS_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS irreversible_holds_open ON irreversible_holds(concurrency_group) "
    "WHERE released_at IS NULL"
)
_SCHEMA_V1 += (
    f"{_WORKER_SESSIONS_SQL};\n{_WORKER_SESSIONS_INDEX_SQL};\n"
    f"{_IRREVERSIBLE_HOLDS_SQL};\n{_IRREVERSIBLE_HOLDS_INDEX_SQL};\n"
)

_BLOBS_SQL = (
    "CREATE TABLE IF NOT EXISTS blobs (sha256 TEXT PRIMARY KEY, size INTEGER NOT NULL, "
    "created_at REAL NOT NULL, last_used_at REAL NOT NULL)"
)
_WORKERS_SQL = (
    "CREATE TABLE IF NOT EXISTS workers (name TEXT PRIMARY KEY, pool TEXT NOT NULL, "
    "lanes INTEGER NOT NULL, host_name TEXT, version TEXT, registered_at REAL NOT NULL, "
    "last_seen_at REAL NOT NULL)"
)
#: v23(M5n) — 워커마다 마지막 보고와 앞 지문. 보고 자체는 서버 메모리에 있고 여기는 덮어 쓴다.
_MACHINES_SQL = (
    "CREATE TABLE IF NOT EXISTS machines (worker TEXT PRIMARY KEY, host_key TEXT, "
    "display_name TEXT, accept_any INTEGER NOT NULL DEFAULT 1, fingerprint_hash TEXT, "
    "fingerprint_json TEXT, previous_json TEXT, changed_at REAL, measured_at REAL, "
    "generation TEXT, report_json TEXT)"
)
_FINGERPRINTS_SQL = (
    "CREATE TABLE IF NOT EXISTS fingerprints (hash TEXT PRIMARY KEY, json TEXT NOT NULL, "
    "first_seen_at REAL NOT NULL)"
)
_MACHINE_CHECKS_SQL = (
    "CREATE TABLE IF NOT EXISTS machine_checks (worker TEXT NOT NULL, preset TEXT NOT NULL, "
    "fingerprint_hash TEXT NOT NULL, job_id INTEGER, submitted_at REAL NOT NULL, "
    "auto INTEGER NOT NULL)"
)
#: 자동 점검 한 번 제한을 찾는 자리. 행은 쌓인다 — 사람의 다시 점검은 몇 번이든(같은 초여도)
_MACHINE_CHECKS_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS machine_checks_key ON machine_checks(worker, preset, "
    "fingerprint_hash)"
)
_JOBS_CHECK_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS jobs_check ON jobs(worker_name, preset, fingerprint_hash, id)"
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
    # `participant` 는 비밀을 받은 토큰 이름(M5l S6) — `leave_submission` 은 그 이름의 bearer
    # 만 쓸 수 있다(남의 leave 비밀로 자기 joiner 행을 지우는 혼동을 막는다).
    # v16 백업은 `migrate()` 의 경계에서 이 DDL 보다 먼저 만들어진다(결정 74).
    17: (
        "CREATE TABLE IF NOT EXISTS submissions ("
        " job_id INTEGER NOT NULL, submission_id TEXT PRIMARY KEY, role TEXT NOT NULL,"
        " capability_hash TEXT NOT NULL, created_at REAL NOT NULL, participant TEXT NOT NULL)",
        "CREATE INDEX IF NOT EXISTS submissions_job ON submissions(job_id)",
    ),
    # v17 → v18(스토어 탭 API 계약 v2): 릴리스 드라이버 실행 대장. 행 하나 = 드라이버 프로세스
    # 하나(`kind` = start · confirm · abort · retry). 잡 표와 무관하고 보존 정리(`rcm gc` ·
    # 청소기)는 이 표를 **건드리지 않는다** — 릴리스 이력은 사람이 지운다. `token_name` 은
    # 그 실행에 발급한 내부 클라이언트 토큰의 이름(끝나면 폐기). v17 백업은 `migrate()` 의 경계에서
    # 이 DDL 보다 먼저 만들어진다(결정 74).
    18: (
        "CREATE TABLE IF NOT EXISTS releases ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, repo TEXT NOT NULL, build_name TEXT NOT NULL,"
        " kind TEXT NOT NULL, started_by TEXT NOT NULL, started_at REAL NOT NULL, pid INTEGER,"
        " log_path TEXT NOT NULL, exit_code INTEGER, finished_at REAL, confirmed_n INTEGER,"
        " confirmed_by TEXT, android_track TEXT, dry_run INTEGER NOT NULL DEFAULT 0,"
        " token_name TEXT)",
        "CREATE INDEX IF NOT EXISTS releases_repo ON releases(repo, id DESC)",
    ),
    # v19: `auto_n` — 이 실행이 «빌드 번호 자동» 으로 시작됐는가. 1 이면 드라이버가 exit 2 로
    # 번호를 물을 때 서버가 로그의 `plan: N` 을 그대로 `--confirm-build-number` 로 이어 준다.
    19: ("ALTER TABLE releases ADD COLUMN auto_n INTEGER NOT NULL DEFAULT 0",),
    # v19 → v20(버전 페이지 · docs/version-page-workplan.md §2.1): 스토어 버전 드래프트 대장. 행
    # 하나 = 사람이 «새 버전 만들기» 로 연 버전(iOS · Android 이름 중 하나 이상). `state` 는
    # creating | editing | running | submitted | discarded | failed. 문안 편집본(`edited_json`)과
    # 이전 버전의 문안(`prefill_json`)은 JSON 문자열 그대로 둔다. releases 처럼 청소기(`rcm gc` ·
    # 보존 정리)는 이 표를 **건드리지 않는다** — 드래프트의 만료는 버전 청소기의 몫이다.
    20: (
        "CREATE TABLE IF NOT EXISTS versions ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, repo TEXT NOT NULL, ios_version TEXT,"
        " android_version TEXT, state TEXT NOT NULL, created_by TEXT NOT NULL,"
        " created_at REAL NOT NULL, last_edit_at REAL, expires_at REAL NOT NULL,"
        " asc_version_id TEXT, prefill_json TEXT, edited_json TEXT, error TEXT,"
        " create_job_id INTEGER, delete_job_id INTEGER, release_id INTEGER,"
        " review_job_id INTEGER, expiry_warned INTEGER NOT NULL DEFAULT 0)",
        "CREATE INDEX IF NOT EXISTS versions_repo ON versions(repo, id DESC)",
    ),
    # v20 → v21(워크플랜 §14-2): `upload_job_id` — 스토어에 바이너리를 올리는 `mode=upload` 잡을
    # 버전 행에 붙인다. review 잡처럼 그 동안 행은 `running` 이고 «버리기» 는 409 다. 올리는 중에
    # 드래프트를 버리면 App Store 버전을 지우는 잡까지 나가는데, 셋 중 가장 되돌리기 어려운 것이
    # 그것만 안 막혀 있었다. `mode=rehearsal` 은 스토어에 쓰지 않으니 붙이지 않는다.
    21: ("ALTER TABLE versions ADD COLUMN upload_job_id INTEGER",),
    # v21 → v22: 잡 링크(`POST /jobs` 의 `link` · `rcm run --link`). 호출자가 잡에 붙인 http(s)
    # 주소 하나와 짧은 라벨 — rcm 은 그것이 PR 인지 모른다(결정 30). 옛 잡은 NULL = 링크 없음이고
    # 지어내지 않는다. 합류자는 링크를 바꾸지 않는다(처음 세운 제출의 것). v21 백업은
    # `migrate()` 의 경계에서 이 DDL 보다 먼저 만들어진다(결정 74).
    22: (
        "ALTER TABLE jobs ADD COLUMN link_url TEXT",
        "ALTER TABLE jobs ADD COLUMN link_label TEXT",
    ),
    # v22 → v23(M5n · docs/m5n-workplan.md §11.1): 머신의 능력과 배치. 잡에는 배치(`placement`)·
    # 지정 워커·그룹 범위·돈 호스트·그때 지문·표본 여부를 박는다. 옛 잡은 기본값(풀 배치 · 풀 범위
    # · 표본)이고 `host_key` NULL 은 읽을 때 로컬 / `worker:<이름>` 으로 푼다(§7.1).
    # `machines` 는 앞 지문을 재시작 너머로 기억한다(「바뀜」을 놓치지 않는다). `fingerprints` 는 잡
    # 상세의 「이 잡이 돈 환경」, `machine_checks` 는 자동 점검의 한 번 제한(결정 95).
    23: (
        "ALTER TABLE jobs ADD COLUMN placement TEXT NOT NULL DEFAULT 'pool'",
        "ALTER TABLE jobs ADD COLUMN display_pool TEXT",
        "ALTER TABLE jobs ADD COLUMN target_worker TEXT",
        "ALTER TABLE jobs ADD COLUMN concurrency_scope TEXT NOT NULL DEFAULT 'pool'",
        "ALTER TABLE jobs ADD COLUMN host_key TEXT",
        "ALTER TABLE jobs ADD COLUMN fingerprint_hash TEXT",
        "ALTER TABLE jobs ADD COLUMN sample INTEGER NOT NULL DEFAULT 1",
        _MACHINES_SQL,
        _FINGERPRINTS_SQL,
        _MACHINE_CHECKS_SQL,
        _MACHINE_CHECKS_INDEX_SQL,
        _JOBS_CHECK_INDEX_SQL,
    ),
    # v23 → v24(M5p · docs/m5p-workplan.md §5.1): 보관함. 잡의 `keep` 은 제출 때 얼린 값이고 옛 잡은
    # 0(보관하지 않는다)이다. 항목은 잡 행보다 오래 사는 표에 둔다. v23 백업은 `migrate()` 경계에서
    # 이 DDL 보다 먼저 만들어진다(결정 74).
    24: (
        "ALTER TABLE jobs ADD COLUMN keep INTEGER NOT NULL DEFAULT 0",
        _SHELF_ITEMS_SQL,
        _SHELF_STATE_INDEX_SQL,
        _SHELF_LATEST_INDEX_SQL,
    ),
    # v24 → v25(M5s · docs/m5s-workplan.md §4.1 · §4.2 · 결정 237): 이 마일스톤의 열을 한 번에
    # 붙인다. 예약은 승격 시각(`not_before`)과 시작 마감(`start_deadline`)이고, 옛 잡은 NULL =
    # 즉시 실행이다. `no_supersede` 는 제출의 `"supersede": false` 를 얼린 값이다 — 재시작해도
    # 잊지 않는다. 반복 사슬(`chain_id` · `retry_of` · `attempt`)과 얼린 반복 정책(`retry_*`)은
    # 여기서 만들기만 하고 쓰는 것은 반복(PR D)이다. v24 백업은 `migrate()` 경계에서 먼저 남는다.
    25: (
        "ALTER TABLE jobs ADD COLUMN not_before REAL",
        "ALTER TABLE jobs ADD COLUMN start_deadline REAL",
        "ALTER TABLE jobs ADD COLUMN no_supersede INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE jobs ADD COLUMN chain_id INTEGER",
        "ALTER TABLE jobs ADD COLUMN retry_of INTEGER",
        "ALTER TABLE jobs ADD COLUMN attempt INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE jobs ADD COLUMN retry_on_exit INTEGER",
        "ALTER TABLE jobs ADD COLUMN retry_interval_seconds INTEGER",
        "ALTER TABLE jobs ADD COLUMN retry_max_attempts INTEGER",
        "ALTER TABLE jobs ADD COLUMN retry_disabled INTEGER NOT NULL DEFAULT 0",
        "CREATE INDEX IF NOT EXISTS jobs_chain ON jobs(chain_id)",
    ),
    # v25 → v26(M5t-b · docs/m5tb-workplan.md §4.9): 워커 세션 표 · 잡의 세션 · 얼린 표식과
    # 유예 · claim 순번 · 로그 오프셋 열(PR2 가 쓴다) · 그룹 홀드 표. 옛 행은 전부 NULL 이다 —
    # 로컬 잡이거나 레거시 잡이라는 뜻이다(T11). v25 백업은 `migrate()` 경계에서 먼저 남는다.
    26: (
        _WORKER_SESSIONS_SQL,
        _WORKER_SESSIONS_INDEX_SQL,
        "ALTER TABLE jobs ADD COLUMN worker_session_id TEXT",
        "ALTER TABLE jobs ADD COLUMN irreversible INTEGER",
        "ALTER TABLE jobs ADD COLUMN disconnect_grace_seconds INTEGER",
        "ALTER TABLE jobs ADD COLUMN claim_seq INTEGER",
        "ALTER TABLE jobs ADD COLUMN log_offset INTEGER",
        "ALTER TABLE jobs ADD COLUMN log_file_bytes INTEGER",
        "ALTER TABLE jobs ADD COLUMN log_complete INTEGER",
        _IRREVERSIBLE_HOLDS_SQL,
        _IRREVERSIBLE_HOLDS_INDEX_SQL,
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


def _backup_pattern(path: Path) -> re.Pattern[str]:
    """자동 백업의 이름 — `rcm.sqlite3.v<n>.bak`. 사람이 만든 `.pre-0.2.4.bak` 은 안 맞는다."""
    return re.compile(rf"^{re.escape(Path(path).name)}\.v(\d+)\.bak$")


def existing_backups(path: Path) -> list[tuple[int, Path]]:
    """`backup/` 에 **실제로 있는** 자동 백업을 버전 오름차순으로. 못 읽으면 `OSError`."""
    pattern = _backup_pattern(path)
    found: list[tuple[int, Path]] = []
    for p in backup_dir(path).iterdir():
        m = pattern.match(p.name)
        if m:
            found.append((int(m.group(1)), p))
    found.sort()
    return found


def newer_database_message(path: Path, version: int) -> str:
    """옛 빌드가 새 DB 를 거절할 때의 문장 — 거절만 하지 않고 길을 붙인다(B2-3).

    복원할 파일은 이 빌드의 `DB_VERSION` 으로 지어내지 않고 `backup/` 에 **있는** 것 중 가장
    높은 것을 가리킨다(리뷰 #87 B P1 · M5l L1): v15 DB 를 v21 빌드로 바로 올리면 백업은
    `v15.bak` 하나라, v16 빌드가 `v16.bak` 을 말하면 없는 파일이다. 하나도 없으면 그렇다고 한다.
    """
    head = f"database schema version {version} is newer than this build ({DB_VERSION}) — "
    tail = f"(and remove {Path(path).name}-wal/-shm), or upgrade"
    try:
        found = existing_backups(path)
    except OSError:
        found = []
    if found:
        _v, newest = found[-1]
        return f"{head}stop the service, restore {newest} {tail}"
    return (
        f"{head}no migration backup found in {backup_dir(path)} — stop the service, "
        f"restore a copy of the database made before the upgrade {tail}"
    )


class StoreError(RuntimeError):
    pass


class ScheduleExists(StoreError):
    """같은 합류 키(프리셋 · 입력 · 소스 신원)의 예약이 이미 있다 — 서버의 409 `schedule_exists`
    (M5s 결정 209 · 227). `job_id` 는 그 예약이다."""

    def __init__(self, job_id: int):
        super().__init__(
            f"job #{job_id} is already scheduled with the same preset, inputs and source"
        )
        self.job_id = job_id


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
    #: 돈 호스트(M5n §7.1). 옛 행(NULL)은 `resolve_host_key` 로 푼다
    host_key: str | None = None
    worker_name: str | None = None
    #: 점검 · 예열 잡은 False — `medians_from` 이 건너뛴다(결정 103)
    sample: bool = True
    #: 예약 잡(M5s)은 대기를 승격 시각(`queued_at`)부터 잰다 — `wait_origin`
    queued_at: datetime | None = None
    not_before: datetime | None = None

    @property
    def wait_origin(self) -> datetime:
        if self.not_before is not None and self.queued_at is not None:
            return self.queued_at
        return self.created_at


@dataclass(frozen=True)
class Claimant:
    """claim 하는 쪽의 신원과 지금 받을 수 있는 것(M5n §5.2).

    로컬 레인은 `worker = LOCAL_WORKER`(`"@local"`) — DB 의 `worker_name` 은 오늘처럼 NULL 이다.
    `capable` · `gated` 는 서버가 claim 직전에 메모리의 보고로 계산한 이름 목록이다.
    """

    worker: str
    pool: str
    host_key: str | None
    fingerprint_hash: str | None
    capable: tuple[str, ...] = ()  # 이 워커가 지금 eligible 인 any-capable 프리셋
    gated: tuple[str, ...] = ()  # checks 로 막힌 프리셋(pool 잡)
    accept_any: bool = True
    #: 지금 설정에 있는 프리셋 전부. 주면 **설정에 없는** 프리셋의 any-capable 잡도 집는다 —
    #: 집은 쪽이 `preset_missing` 으로 바로 끝낸다(pool 잡과 같다). 안 집으면 아무의 `capable`
    #: 에도 없는 이름이라 영원히 대기한다(reload 로 지우거나 이름을 바꾼 프리셋 · 엣지 검증)
    known: tuple[str, ...] = ()
    #: 이 워커가 **어느 갈래로도** 집지 못하는 프리셋(M5t-a 결정 254) — 서버의 후보 함수가 이
    #: 워커를 뺀 `local_only` 프리셋이다. SQL 은 Python 함수를 못 부르므로 이름으로 넘긴다
    excluded: tuple[str, ...] = ()


@dataclass(frozen=True)
class MachineRow:
    """`machines` 표의 한 행 — 워커의 마지막 보고와 앞 지문(M5n §11.1)."""

    worker: str
    host_key: str | None
    display_name: str | None
    accept_any: bool
    fingerprint_hash: str | None
    fingerprint_json: str | None
    previous_json: str | None
    changed_at: datetime | None
    measured_at: datetime | None
    generation: str | None
    report_json: str | None
    #: 이번 upsert 가 지문을 바꿨나(앞 지문이 있었고 달랐다). 읽기에서는 False
    changed: bool = False


#: 로컬 레인 묶음의 워커 id(`core.capability.LOCAL_WORKER` 와 같은 값). 원격 이름은 `@` 를 못 쓴다
LOCAL_WORKER_ID = "@local"


class LaneBusy(StoreError):
    """그 워커의 그 레인에 이미 running·cancelling 잡이 있다 — 레인 과할당 금지(M5b-2)."""


class SessionFenced(StoreError):
    """claim 하는 세션이 그사이 펜싱됐다(M5t-b §4.2). 긴 대기 claim 은 시작할 때 한 번 본 세션
    판정을 믿을 수 없다 — 그 사이 같은 이름의 새 프로세스가 등록했을 수 있다."""


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


@dataclass(frozen=True)
class SessionRow:
    """워커 세션 행(M5t-b 결정 265). `fenced_at` 이 None 이면 그 이름의 현재 세션이다."""

    id: str
    worker_name: str
    registered_at: datetime | None
    fenced_at: datetime | None
    fenced_by: str | None
    orphan_unverified: bool


@dataclass(frozen=True)
class SessionOpened:
    """`open_session` 이 한 트랜잭션에서 한 일 — 닫은 잡(레거시 포함) · 푼 홀드 · 펜싱한 세션."""

    closed: list[int]
    legacy: list[int]
    released: list[int]
    fenced: list[str]


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


def _log_complete(row: sqlite3.Row, log_bytes: int | None) -> int | None:
    """끝나는 잡의 `log_complete` 칸(M5t-b §4.6) — 1 · 0 · NULL(모름)."""
    if row["log_complete"] == 0:
        return 0  # 짧은 파일에서 미리 고정됐다(T16) — 뒤의 어떤 보고도 되돌리지 않는다
    if row["worker_name"] is None:
        return 1  # 로컬 레인 — 서버가 직접 쓴다
    if log_bytes is None:
        return row["log_complete"]
    return 1 if int(row["log_offset"] or 0) >= log_bytes else 0


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
    shelf_enabled: bool = False,
    shelf_available: bool = True,
) -> None:
    """묶음 행 하나를 쓴다(열려 있는 트랜잭션 안에서). 이미 발행된 묶음은 **불변**이다.

    만료는 `ready` 일 때만 박는다 — 버리거나 깨진 묶음은 지울 파일이 없다. 발행된 묶음은 만료가
    반드시 있다(만료 없는 행을 만들면 sweep 이 영영 못 가져간다, §9 ⑤).

    묶음을 `ready` 로 쓰는 곳은 이 함수 하나다(로컬 `finish(bundle=…)` · 원격 `publish_bundle`).
    그래서 보관 요청 행도 **여기서** 세운다(M5p 결정 129) — 잡이 keep 이면 ready 를 쓴 직후 같은
    트랜잭션에서. 불변 조기 반환보다 뒤라 같은 묶음을 두 번 내도 행은 그대로다.
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
    if ready and _job_keeps(conn, job_id):
        _request_shelf_tx(conn, job_id, now, shelf_enabled, shelf_available)


#: `rcm keep` 이 `requested` 를 주려면 묶음 만료까지 이만큼은 남아야 한다(§15.1) — copier 가 집기
#: 전에 TTL 청소가 가져가는 일을 줄인다(그래도 오면 `mark_bundles_purged` 가 조건부라 막는다).
KEEP_EXPIRY_MARGIN_SECONDS = 60.0
#: `mark_shelf(if_last_used=…)` 를 안 줬다는 표식(None 은 「비어 있을 때」라는 뜻이라 따로 둔다).
_ANY: Any = object()
#: `mark_shelf` 가 받는 열 — 키와 상태는 따로 받는다.
_SHELF_FIELDS = frozenset(SHELF_COLUMNS) - {"job_id", "state"}
#: `job_artifacts` 를 거르는 조건 — 그 잡의 보관 요청이 진행 중이 아니다(M5p 결정 130).
_NOT_SHELVING = (
    "job_id NOT IN (SELECT job_id FROM shelf_items WHERE state IN ('pending','copying'))"
)


#: 합류가 비교하는 얼린 반복 정책의 열(#256 검증) — 세 칸과 끄기가 모두 같아야 붙는다
_RETRY_JOIN_SQL = (
    "AND retry_on_exit IS ? AND retry_interval_seconds IS ? AND retry_max_attempts IS ? "
    "AND retry_disabled=? "
)


def _frozen_retry(
    retry: RetryPolicy | None, disabled: bool
) -> tuple[int | None, int | None, int | None, int]:
    """제출이 얼릴 반복 정책의 합류 비교값 — `_RETRY_JOIN_SQL` 의 차례, 잡 열과 같은 모양.

    정책이 다른 잡에 붙으면 합류자는 그 잡의 정책을 받는다 — 간격이나 상한만 달라도 사용자가
    기대한 반복이 아니다(reload 로 `max_attempts` 만 바꾼 뒤의 제출)."""
    if retry is None:
        return None, None, None, 0
    return (
        int(retry.on_exit),
        int(retry.interval_seconds),
        int(retry.max_attempts),
        1 if disabled else 0,
    )


def _job_keeps(conn: sqlite3.Connection, job_id: int) -> bool:
    row = conn.execute("SELECT keep FROM jobs WHERE id=?", (job_id,)).fetchone()
    return row is not None and bool(row["keep"])


def _request_shelf_tx(
    conn: sqlite3.Connection,
    job_id: int,
    now: datetime,
    enabled: bool,
    available: bool = True,
) -> bool:
    """보관 요청 행을 세운다(열려 있는 트랜잭션 안에서). 이미 행이 있으면 그대로 둔다.

    잡의 열을 **복사한다** — 항목은 잡 행보다 오래 산다(§4.5). 원격 잡은 이 시점에 아직
    `running` 이라 잡 상태 · 끝난 시각은 잡이 끝났을 때만 채운다(나머지는 `Store.finish` 가 같은
    트랜잭션에서 고친다, §15.1). 보관함이 꺼졌거나 쓸 수 없으면 `pending` 을 거치지 않고 곧장
    `failed` 로 세운다 — 묶음을 붙잡지 않는다.
    """
    job = conn.execute(
        "SELECT preset, inputs_json, source_json, sha, tree_hash, requester_label, state, "
        "finished_at FROM jobs WHERE id=?",
        (int(job_id),),
    ).fetchone()
    if job is None:
        return False
    src = json.loads(job["source_json"])
    source = Source(
        mode=src.get("mode", "tree"),
        repo=src.get("repo"),
        branch=src.get("branch"),
        ref=src.get("ref"),
    )
    sha = job["sha"] if source.mode == "git_ref" else (job["tree_hash"] or src.get("tree_hash"))
    terminal = job["state"] in TERMINAL_STATES
    ts = _ts(now)
    state, reason = shelf_rules.STATE_PENDING, None
    if not available:
        state, reason = shelf_rules.STATE_FAILED, "unavailable"
    elif not enabled:
        state, reason = shelf_rules.STATE_FAILED, "disabled"
    cur = conn.execute(
        "INSERT OR IGNORE INTO shelf_items (job_id, state, preset, inputs_json, repo, branch, sha, "
        "source_mode, requester, job_state, job_finished_at, requested_at, reason_code, failed_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            int(job_id),
            state,
            job["preset"],
            job["inputs_json"],
            repo_of(source),
            branch_of(source),
            sha,
            source.mode,
            job["requester_label"],
            job["state"] if terminal else None,
            job["finished_at"] if terminal else None,
            ts,
            reason,
            ts if reason is not None else None,
        ),
    )
    return cur.rowcount == 1


def _ready_bundle(conn: sqlite3.Connection, job_id: int) -> bool:
    """그 잡의 묶음이 지금 `ready` 이고 아직 안 지워졌나."""
    row = conn.execute(
        "SELECT state, purged_at FROM job_artifacts WHERE job_id=?", (int(job_id),)
    ).fetchone()
    return row is not None and row["state"] == artifacts.READY and row["purged_at"] is None


def _shelf_item(row: sqlite3.Row) -> ShelfItem:
    values = {c: row[c] for c in SHELF_COLUMNS}
    raw_args = values["reason_args"]
    values["reason_args"] = json.loads(raw_args) if raw_args else None
    return ShelfItem(**values)


#: 합류할 수 있는 상태 — 활성 상태에서 `cancelling` 을 뺀 것. 취소 중인 잡은 결과를 줄 수 없다
JOINABLE_STATES = ACTIVE_STATES - {CANCELLING}


def _open_hold_sql(host_key: str | None, pool: str) -> tuple[str, list[Any]]:
    """후보 잡 `c`(유효 그룹 `eg` · 범위 `es`)를 막는 열린 그룹 홀드가 없다는 조건(결정 330).

    범위는 넓게 본다: 어느 한쪽이 `global` 이면 막고, 어느 한쪽이 `host` 면 호스트가 같거나 모를
    때 막고, 풀이 같으면 막는다. 좁게 봐서 놓치면 확인 없이 그룹이 열린다(fail-open)."""
    return (
        "NOT EXISTS (SELECT 1 FROM irreversible_holds h WHERE h.released_at IS NULL "
        "AND h.concurrency_group = c.eg AND (h.concurrency_scope = ? OR c.es = ? "
        "OR ((h.concurrency_scope = ? OR c.es = ?) "
        "AND (? IS NULL OR h.host_key IS NULL OR h.host_key = ?)) OR h.pool = ?))",
        [SCOPE_GLOBAL, SCOPE_GLOBAL, SCOPE_HOST, SCOPE_HOST, host_key, host_key, pool],
    )


class Store:
    """SQLite 저장소. 한 프로세스 안에서 여러 스레드가 같이 쓴다."""

    def __init__(self, path: str | Path, *, log: Callable[[str], None] | None = None):
        self.path = Path(path)
        self._local = threading.local()
        self._lock = threading.Lock()
        self.open_connections = 0  # 지금 열린 연결 수(스레드마다 하나) — 누수 감시
        self._holds: dict[int, int] = {}  # 지금 내려보내는 중인 묶음(M5e) — 프로세스 안에서만
        #: 보관함(M5p §15.1). 새 보관을 받나 — App 이 설정(`shelf_max_bytes > 0`)을 읽고 넣는다.
        #: 기본이 False 인 이유: 설정을 모르는 곳(`rcm gc` 사본 · 시험)이 보관 행을 `pending` 으로
        #: 세워 묶음을 붙잡으면 안 된다. 꺼져 있으면 keep 잡의 행은 곧장 `failed(disabled)` 다.
        self.shelf_enabled = False
        #: 보관함 폴더를 쓸 수 있나 — 기동 화해(`reconcile_shelf_on_start`)가 정한다(§5.3).
        self.shelf_available = True
        #: 표식 프리셋 → (유효 그룹, 범위)(M5t-b 결정 330). App 이 설정을 읽을 때마다 넣는다.
        #: claim 이 그룹 NULL 인 옛 대기 잡의 유효 그룹을 이 표로 정하고 표식을 얼린다
        self.irreversible: dict[str, tuple[str, str]] = {}
        #: 설정에 있는 프리셋 이름. 레거시 잡(얼린 값 없음)의 표식 판정에 쓴다 — 설정에 없으면
        #: 「모름」이고 닫힌 쪽으로 본다(결정 339-d). None 이면 설정을 모르는 저장소다(시험 · 사본)
        self.known_presets: frozenset[str] | None = None
        #: 마지막 `recover_on_start` 가 정리한 예약 — `(expired, promoted)`(M5s)
        self.schedule_recovered: tuple[list[int], list[int]] = ([], [])
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
        try:
            found = existing_backups(self.path)
        except OSError as e:
            # 읽기 권한 없는 `backup/` — 새 백업은 써졌지만 옛것은 못 센다. 조용히 쌓이게 두지
            # 않는다(리뷰 #87 B P2).
            self._log(f"warning: could not list {backup_dir(self.path)} to prune old backups: {e}")
            return
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
            link=(
                JobLink(url=row["link_url"], label=row["link_label"]) if row["link_url"] else None
            ),
            placement=row["placement"] or PLACEMENT_POOL,
            display_pool=row["display_pool"],
            target_worker=row["target_worker"],
            concurrency_scope=row["concurrency_scope"] or SCOPE_POOL,
            host_key=row["host_key"],
            fingerprint_hash=row["fingerprint_hash"],
            sample=bool(row["sample"]) if row["sample"] is not None else True,
            keep=bool(row["keep"]),
            not_before=_dt(row["not_before"]),
            start_deadline=_dt(row["start_deadline"]),
            no_supersede=bool(row["no_supersede"]),
            chain_id=row["chain_id"],
            retry_of=row["retry_of"],
            attempt=int(row["attempt"] or 1),
            retried_as=self._retried_as(conn, row),
            retry_on_exit=row["retry_on_exit"],
            retry_interval_seconds=row["retry_interval_seconds"],
            retry_max_attempts=row["retry_max_attempts"],
            retry_disabled=bool(row["retry_disabled"]),
            worker_session_id=row["worker_session_id"],
            irreversible=None if row["irreversible"] is None else bool(row["irreversible"]),
            disconnect_grace_seconds=row["disconnect_grace_seconds"],
            claim_seq=row["claim_seq"],
        )

    @staticmethod
    def _retried_as(conn: sqlite3.Connection, row: sqlite3.Row) -> int | None:
        """`retried` 로 끝난 시도의 다음 시도 id(M5s). 다른 상태면 묻지도 않는다 — 행마다 질의를
        붙이지 않으려는 것이다. 사슬 인덱스(`jobs_chain`)로 찾는다."""
        if row["state"] != RETRIED or row["chain_id"] is None:
            return None
        found = conn.execute(
            "SELECT id FROM jobs WHERE chain_id=? AND retry_of=? ORDER BY id LIMIT 1",
            (row["chain_id"], row["id"]),
        ).fetchone()
        return int(found["id"]) if found is not None else None

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

    def list_scheduled(self) -> list[Job]:
        """예약 잡(M5s) — 승격 시각 순, 같으면 id 순. 활성 집합 밖이라 `list_active` 에 없다."""
        return self._jobs("SELECT * FROM jobs WHERE state=? ORDER BY not_before, id", (SCHEDULED,))

    def list_recent(self, limit: int) -> list[Job]:
        """최근 완료 잡 `limit` 개, 새것부터.

        **두 단계로 고른다.** `SELECT *` 한 방이면 `jobs_recent` 가 커버링이 아니게 돼 플래너가
        `jobs_state` 로 물러서고 종료 잡 **전체**를 정렬한다 — 여덟 개를 고르려고 5만 개를 줄
        세우는 셈이라 22.5 ms 다. id 만 커버링 인덱스로 고른 뒤 그 행만 읽으면 0.02 ms 다.
        """
        if limit <= 0:
            return []
        # 사슬의 중간 시도(`retried`)는 넣지 않는다(M5s §4.2) — 12번 반복이 다른 결과 8개를
        # 밀어낸다. 사슬은 마지막 시도 한 행이다
        shown = sorted(TERMINAL_STATES - CHAIN_LINK_STATES)
        marks = ",".join("?" * len(shown))
        ids = [
            int(r["id"])
            for r in self._conn().execute(
                f"SELECT id FROM jobs WHERE state IN ({marks}) "
                "ORDER BY finished_at DESC, id DESC LIMIT ?",
                (*shown, limit),
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

    def list_jobs_by_preset(self, presets: Iterable[str], limit: int) -> list[Job]:
        """프리셋 이름들의 잡을 새것부터 `limit` 개(스토어 탭의 역할 행 — 계약 v2 `jobs[]`)."""
        names = sorted(set(presets))
        if not names or limit <= 0:
            return []
        marks = ",".join("?" * len(names))
        return self._jobs(
            f"SELECT * FROM jobs WHERE preset IN ({marks}) ORDER BY id DESC LIMIT ?",
            (*names, int(limit)),
        )

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
        # `retried` 시도는 표본이 아니다(M5s §4.2) — 스크립트가 「아직」이라며 일찍 끝난 시간이다
        sampled = sorted(TERMINAL_STATES - CHAIN_LINK_STATES)
        marks = ",".join("?" * len(sampled))
        rows = (
            self._conn()
            .execute(
                f"SELECT key, pool, state, created_at, started_at, finished_at, host_key, "
                f"worker_name, sample, queued_at, not_before FROM jobs "
                f"WHERE state IN ({marks}) AND started_at >= ? AND finished_at IS NOT NULL "
                "AND sample != 0 ORDER BY id",
                (*sampled, _ts(since)),
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
                host_key=r["host_key"],
                worker_name=r["worker_name"],
                sample=bool(r["sample"]) if r["sample"] is not None else True,
                queued_at=_dt(r["queued_at"]),
                not_before=_dt(r["not_before"]),
            )
            for r in rows
        ]

    def list_step_sample_ids(
        self,
        key: str,
        *,
        since: datetime,
        limit: int,
        host_key: str | None = None,
        local_host_key: str | None = None,
    ) -> list[int]:
        """그 key 의 최근 **성공** 잡 id, 늦게 끝난 것부터 `limit` 개.

        단계 실측 중앙값의 표본이다. `jobs_key_finished ON jobs(key, finished_at DESC)`(v13)를
        타고 그 key 의 꼬리만 읽는다 — **45일치 표본 전체의 마커를 훑지 않는다**. `events` 에는
        `kind` 로 훑는 인덱스가 없어서(`events_job ON events(job_id, id)` 하나뿐) 그렇게 설계하면
        `/api/status` 가 보존된 이벤트 수에 선형으로 끌려간다. M5f 결정 49 가 `list_sample_rows`
        로 이미 한 번 고친 함정이다.

        `sample_policy` 는 **보지 않는다** — 항상 `succeeded` 다. 실패한 잡의 단계 소요는
        「평소 얼마나 걸리나」의 표본이 아니다. 실패는 대개 단계를 일찍 끊는다.

        `host_key` 가 있으면 **그 호스트에서 돈** 잡만(M5n §7.1 — 다른 호스트로 물러나지 않는다).
        옛 행(`host_key` NULL)은 `local_host_key` 면 로컬 레인 잡(`worker_name` NULL)으로,
        `worker:<이름>` 이면 그 워커의 잡으로 푼다. `sample = 0`(점검 · 예열)은 언제나 뺀다.
        """
        host_sql = ""
        host_params: tuple[Any, ...] = ()
        if host_key is not None:
            legacy = "0"
            legacy_params: tuple[Any, ...] = ()
            if local_host_key is not None and host_key == local_host_key:
                legacy = "worker_name IS NULL"
            elif host_key.startswith("worker:"):
                legacy = "worker_name = ?"
                legacy_params = (host_key[len("worker:") :],)
            host_sql = f" AND (host_key = ? OR (host_key IS NULL AND {legacy}))"
            host_params = (host_key, *legacy_params)
        rows = (
            self._conn()
            .execute(
                # `INDEXED BY` 는 계약이다. 플래너는 통계 없이 `jobs_recent`(state, finished_at)
                # 를 고르는데, 그러면 key 와 무관하게 45일치 성공 잡을 전부 훑는다 — 피하려던
                # 바로 그 스캔이다. 여기서는 key 하나의 꼬리만 읽어야 한다.
                "SELECT id FROM jobs INDEXED BY jobs_key_finished "
                "WHERE key=? AND state=? AND finished_at IS NOT NULL "
                f"AND finished_at >= ? AND sample != 0{host_sql} "
                "ORDER BY finished_at DESC, id DESC LIMIT ?",
                (key, SUCCEEDED, _ts(since), *host_params, int(limit)),
            )
            .fetchall()
        )
        return [int(r["id"]) for r in rows]

    def finished_at_for(self, job_ids: Iterable[int]) -> dict[int, datetime]:
        """잡 id → 종료 시각. 마커를 Progress 로 접을 때 마지막 단계를 닫는 데 쓴다.

        `progress_from_markers` 는 끝 마커가 없는 마지막 단계를 `finished_at` 으로 닫는다 —
        `gate` 의 마지막 단계가 딱 그 꼴이라 이 값이 없으면 사고가 난 그 단계가 표본에서
        통째로 빠진다. 기본키 하나로 **한 문장**이다(표본 잡 수·이벤트 수와 무관하다).
        """
        ids = tuple(job_ids)
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        rows = self._conn().execute(f"SELECT id, finished_at FROM jobs WHERE id IN ({marks})", ids)
        return {int(r["id"]): at for r in rows if (at := _dt(r["finished_at"])) is not None}

    def list_samples(self, since: datetime) -> list[Job]:
        """표본 후보: 시작·종료 시각이 있는 종료 잡. 정책 필터는 순수 계층이 한다.
        사슬의 중간 시도(`retried`)는 빠진다(M5s §4.2)."""
        sampled = sorted(TERMINAL_STATES - CHAIN_LINK_STATES)
        marks = ",".join("?" * len(sampled))
        return self._jobs(
            f"SELECT * FROM jobs WHERE state IN ({marks}) AND started_at >= ? "
            "AND finished_at IS NOT NULL ORDER BY id",
            (*sampled, _ts(since)),
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

    def failure_names(self, job_id: int) -> list[str]:
        """그 잡의 실패 이름 대장 — **잡이 찍은 순서**(`seq`). 없으면 빈 목록(M5o 결정 119)."""
        return [
            str(r[0])
            for r in self._conn()
            .execute("SELECT name FROM job_failures WHERE job_id=? ORDER BY seq", (job_id,))
            .fetchall()
        ]

    def failure_stats(
        self, job_id: int, key: str, finished_at: datetime | None, *, window: int
    ) -> tuple[list[FailureRow], int, int]:
        """이 잡이 지목한 이름들이 **같은 key 의 최근 창**에서 몇 번 보였나.

        창은 `finished_at` 을 **앵커로** 그 잡까지 최근 `window` 개다(취소·유실은 아무 말도
        안 하므로 뺀다). 이 잡이 늘 창의 맨 앞이라 자기 이름의 `seen` 은 1 이상이고, 한 달
        뒤에 같은 잡을 다시 열어도 **같은 답**이 나온다(창이 흘러가지 않는다 — 명세 §2.1).

        프로세스가 뜨기 전에 서버가 닫은 잡(`WINDOW_EXCLUDED_CODES` · `tool_missing`, M5j G4)도
        취소·유실과 같이 뺀다 — 스크립트에 대해 아무 말도 못 한 잡이 분모에 들면 결정적 실패
        셋이 「4 중 3 · intermittent?」 로 보인다(결정 68 의 분모는 스크립트가 **돈** 잡이다).

        돌려주는 것은 `(줄 목록, 창의 잡 수, 이름 없이 실패한 잡 수)`. 줄 순서는 그 잡이 찍은
        순서(`seq`)다.
        """
        conn = self._conn()
        at = _ts(finished_at) if finished_at is not None else None
        if at is None or window < 1:
            return [], 0, 0
        states = (SUCCEEDED, FAILED, TIMED_OUT)
        marks = ",".join("?" * len(states))
        excluded = ",".join("?" * len(WINDOW_EXCLUDED_CODES))
        ids = [
            int(r[0])
            for r in conn.execute(
                f"SELECT id FROM jobs WHERE key=? AND state IN ({marks}) "
                "AND finished_at IS NOT NULL "
                f"AND (summary_code IS NULL OR summary_code NOT IN ({excluded})) "
                "AND (finished_at < ? OR (finished_at = ? AND id <= ?)) "
                "ORDER BY finished_at DESC, id DESC LIMIT ?",
                (key, *states, *WINDOW_EXCLUDED_CODES, at, at, job_id, window),
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
                    "WHERE purged_at IS NULL OR reserved_bytes > 0) "
                    # M5s: 사슬이 살아 있으면 뿌리를 남긴다 — 취소 capability 가 뿌리에
                    # 있다(결정 228)
                    f"AND id NOT IN (SELECT chain_id FROM jobs WHERE chain_id IS NOT NULL "
                    f"AND state NOT IN ({marks})) "
                    # M5t-b: 열린 그룹 홀드의 잡은 지우지 않는다 — 지우면 홀드가 근거를 잃는다
                    # mutcheck: hold-row-deleted-by-gc
                    "AND id NOT IN (SELECT job_id FROM irreversible_holds "
                    "WHERE released_at IS NULL)",
                    (*sorted(TERMINAL_STATES), _ts(cutoff), *sorted(TERMINAL_STATES)),
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
                # 남는 것은 풀린 홀드뿐이다 — 잡 행과 함께 감사 이력도 보존 기간을 따른다
                conn.execute(f"DELETE FROM irreversible_holds WHERE job_id IN ({id_marks})", ids)
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
            _upsert_bundle(
                conn,
                int(job_id),
                result,
                now=now,
                expires_at=expires_at,
                shelf_enabled=self.shelf_enabled,
                shelf_available=self.shelf_available,
            )
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
            if decision.purge and self._shelving(conn, job_id):
                # 보관함이 아직 이 묶음을 푸는 중이다(M5p 결정 130) — 지금 지우면 보관이 빈손이다.
                # `acked_at` 은 적고 지우기만 미룬다. 보관이 끝나면 copier 가
                # `release_deferred_bundle` 로 그때 지운다. 같은 트랜잭션이라 판정과 보관 요청이
                # 엇갈리지 않는다.
                decision = artifacts.AckDecision(
                    decision.status, False, decision.record, "shelving", deferred=True
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
    def _shelving(conn: sqlite3.Connection, job_id: int) -> bool:
        """그 잡의 보관 요청이 아직 끝나지 않았나(`pending` · `copying`)."""
        row = conn.execute(
            "SELECT state FROM shelf_items WHERE job_id=?", (int(job_id),)
        ).fetchone()
        return row is not None and row["state"] in shelf_rules.IN_FLIGHT_STATES

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
        """TTL 이 지난 묶음. 만료 없는 행과 이미 사라진 것은 절대 대상이 아니다(§8).

        보관함이 아직 푸는 중(`pending` · `copying`)인 묶음도 뺀다(M5p 결정 130). 그 보관이 영원히
        안 끝나면 청소기의 안전판(`time_out_shelf_requests`)이 그 행을 접어 여기로 돌려보낸다.
        """
        marks = ",".join("?" * len(artifacts.GONE_STATES))
        rows = (
            self._conn()
            .execute(
                f"SELECT job_id, state, expires_at, bundle_bytes FROM job_artifacts "
                f"WHERE expires_at IS NOT NULL AND expires_at <= ? AND state NOT IN ({marks}) "
                f"AND {_NOT_SHELVING} ORDER BY job_id LIMIT ?",
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
        """지웠다고 표시하고 예약을 반납한다. 이미 표시된 것은 다시 세지 않는다.

        보관함이 푸는 중인 묶음은 표시하지 않는다(M5p §15.2) — `bundles_due` 를 읽은 뒤 그 사이
        `rcm keep` 이 보관을 요청했으면 행은 `ready` 로 남고, copier 가 파일이 없는 것을 보고
        보관을 접은 뒤 다음 회차가 표시한다. 「pending 행만 남고 묶음은 purged」는 없다.
        """
        ids = [int(i) for i in job_ids]
        if not ids:
            return 0
        id_marks = ",".join("?" * len(ids))
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            cur = conn.execute(
                f"UPDATE job_artifacts SET state=?, purged_at=?, reserved_bytes=0 "
                f"WHERE job_id IN ({id_marks}) AND purged_at IS NULL AND {_NOT_SHELVING}",
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

    # ── 보관함 (M5p) ─────────────────────────────────────────────────────────

    def _turn_keep_on(self, conn: sqlite3.Connection, job_id: int, now: datetime) -> None:
        """잡의 keep 을 켠다(열려 있는 트랜잭션 안에서). 묶음이 이미 ready 면 보관 행도 세운다.

        keep 을 켜는 모든 길(합류 · supersede · `set_job_keep`)이 같은 트랜잭션에서 묶음을 본다
        (§15.1). 원격 잡은 묶음 ready 와 finish 가 다른 트랜잭션이라, 그 사이에 켠 keep 은
        `_upsert_bundle` 을 이미 지나쳤다 — 여기서 세우지 않으면 행이 영영 안 선다.
        """
        conn.execute("UPDATE jobs SET keep=1 WHERE id=? AND keep=0", (int(job_id),))
        if _ready_bundle(conn, job_id):
            _request_shelf_tx(conn, job_id, now, self.shelf_enabled, self.shelf_available)

    def _bundle_verdict(self, conn: sqlite3.Connection, job_id: int, now: datetime) -> str:
        """`set_job_keep` 이 보는 묶음 — `ready` · `moving`(아직 오는 중) · `gone` · `absent` ·
        `none`(종결인데 ready 가 아니다)."""
        b = conn.execute(
            "SELECT state, expires_at, acked_at, purged_at FROM job_artifacts WHERE job_id=?",
            (int(job_id),),
        ).fetchone()
        if b is None:
            return "absent"
        state = b["state"]
        if state in artifacts.PROGRESS_STATES:
            return "moving"
        if state in artifacts.GONE_STATES or state == artifacts.UNAVAILABLE:
            return "gone"
        if state != artifacts.READY:
            return "none"
        if b["purged_at"] is not None:
            return "gone"
        expiry = b["expires_at"]
        if expiry is None or float(expiry) - float(_ts(now)) < KEEP_EXPIRY_MARGIN_SECONDS:
            return "gone"  # copier 가 집기 전에 TTL 청소가 가져간다
        if b["acked_at"] is not None and self._join_count(conn, job_id) == 0:
            return "gone"  # 소유자가 받고 확인했다 — 그 요청 안에서 지워졌거나 지워질 참이다
        return "ready"

    def set_job_keep(self, job_id: int, now: datetime, *, enabled: bool | None = None) -> str:
        """`rcm keep N` — 한 `BEGIN IMMEDIATE` 안에서 판정하고 쓴다(§4.1 · §15.1).

        결과는 `armed` · `requested` · `already_requested` · `retouched` · `retried` · `no_bundle`
        · `gone` · `disabled` 중 하나다. 판정과 잡 종료 · ack 가 같은 쓰기 잠금 아래에 있어서
        「keep 1 · 묶음 ready · 보관 행 없음」과 「requested 인데 묶음은 지워짐」이 생기지 않는다.

        `enabled` 를 안 주면 `self.shelf_enabled` 다. 꺼져 있으면 **새로 보관할** 결과(armed ·
        requested · retried)만 `disabled` 가 되고, 이미 있는 항목의 되살리기는 된다(§3.1).
        보관함 폴더가 `unavailable` 이면 무엇이든 `disabled` 다. 없는 잡은 `StoreError`.
        프리셋에 `artifacts` 가 없는 도는 잡은 서버가 이 함수 **전에** 거른다(설정은 store 밖이다).
        """
        enabled = self.shelf_enabled if enabled is None else bool(enabled)
        conn = self._conn()
        ts = _ts(now)
        conn.execute("BEGIN IMMEDIATE")
        try:
            job = conn.execute("SELECT state FROM jobs WHERE id=?", (int(job_id),)).fetchone()
            if job is None:
                conn.execute("ROLLBACK")
                raise StoreError(f"job {job_id} not found")
            row = conn.execute(
                "SELECT state FROM shelf_items WHERE job_id=?", (int(job_id),)
            ).fetchone()
            current = row["state"] if row is not None else None
            if not self.shelf_available:
                result = "disabled"
            elif current in shelf_rules.IN_FLIGHT_STATES:
                result = "already_requested"
            elif current in shelf_rules.SHELVED_STATES:
                result = "retouched"
            else:
                bundle = self._bundle_verdict(conn, job_id, now)
                if bundle == "ready":
                    result = "retried" if current == shelf_rules.STATE_FAILED else "requested"
                elif bundle == "gone":
                    result = "gone"
                elif job["state"] not in TERMINAL_STATES and bundle in ("moving", "absent"):
                    # 끝난 잡의 묶음은 더 오지 않는다 — 아직 오는 중으로 남았으면 끊긴 것이다
                    # (업로드 도중 유실, P2 격리 검증 D2). 그때는 `no_bundle(interrupted)`
                    result = "armed"
                else:
                    result = "no_bundle"
                if result in ("armed", "requested", "retried") and not enabled:
                    result = "disabled"
            if result in ("armed", "requested", "retried", "already_requested", "retouched"):
                conn.execute("UPDATE jobs SET keep=1 WHERE id=? AND keep=0", (int(job_id),))
            if result == "requested":
                if current is not None:  # 묘비(손으로 지운 직후) — 새 요청으로 갈아 끼운다
                    conn.execute("DELETE FROM shelf_items WHERE job_id=?", (int(job_id),))
                _request_shelf_tx(conn, job_id, now, True, True)
            elif result == "retried":
                conn.execute(
                    "UPDATE shelf_items SET state=?, requested_at=?, reason_code=NULL, "
                    "reason_args=NULL, error_code=NULL, failed_at=NULL WHERE job_id=?",
                    (shelf_rules.STATE_PENDING, ts, int(job_id)),
                )
            elif result == "retouched":
                # 되살리기 — 1시간 규칙 없이 시계를 돌린다(§15.1)
                conn.execute(
                    "UPDATE shelf_items SET state=?, last_used_at=?, stale_since=NULL "
                    "WHERE job_id=?",
                    (shelf_rules.STATE_ACTIVE, ts, int(job_id)),
                )
            conn.execute("COMMIT")
            return result
        except StoreError:
            raise
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def release_deferred_bundle(self, job_id: int, now: datetime | None = None) -> Path | None:
        """미뤄 둔 ack 삭제를 이제 할 수 있으면 묶음을 `purged` 로 표시하고 그 폴더를 돌려준다.

        copier 가 `finish_shelf_item` 뒤에 부른다(§15.2). 한 트랜잭션에서 「ack 됨 · 합류자 0 ·
        진행 중인 보관 행 없음 · 아직 안 지움」을 본다 — 그새 합류가 끼었으면 TTL 까지 둔다.
        파일은 부르는 쪽(서버)이 지운다. 할 일이 없으면 None.

        같은 폴더를 두 번 지우는 일이 생길 수 있다(copier 의 이 경로와 ack 의 purge 가 엇갈릴 때,
        격리 검증 I4). 해는 없다: 이 트랜잭션은 `purged_at IS NULL` 일 때만 표시하므로 회계는 한
        번만 줄고, 지우는 쪽(`remove_under_root` · `Janitor._remove_tree`)은 이미 없는 폴더에
        False 를 돌려줄 뿐 예외를 내지 않는다.
        """
        when = now if now is not None else datetime.now(UTC)
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            b = conn.execute(
                "SELECT state, acked_at, purged_at FROM job_artifacts WHERE job_id=?",
                (int(job_id),),
            ).fetchone()
            if (
                b is None
                or b["state"] != artifacts.READY
                or b["acked_at"] is None
                or b["purged_at"] is not None
                or self._join_count(conn, job_id) != 0
                or self._shelving(conn, job_id)
            ):
                conn.execute("ROLLBACK")
                return None
            conn.execute(
                "UPDATE job_artifacts SET state=?, purged_at=?, reserved_bytes=0 WHERE job_id=?",
                (artifacts.PURGED, _ts(when), int(job_id)),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return self._artifacts_root() / str(int(job_id))

    def claim_shelf_item(self, now: datetime) -> ShelfItem | None:
        """`pending` 가운데 가장 오래 기다린 것(같으면 작은 잡 번호)을 `copying` 으로 집는다.

        잡이 아직 안 끝났어도 집는다(원격 잡은 묶음이 먼저 ready 가 된다, §15.1). copier 가 둘이어도
        `BEGIN IMMEDIATE` 와 조건부 UPDATE 로 한 번만 집힌다.
        """
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT job_id FROM shelf_items WHERE state=? "
                "ORDER BY requested_at, job_id LIMIT 1",
                (shelf_rules.STATE_PENDING,),
            ).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                return None
            conn.execute(
                "UPDATE shelf_items SET state=? WHERE job_id=? AND state=?",
                (shelf_rules.STATE_COPYING, int(row["job_id"]), shelf_rules.STATE_PENDING),
            )
            got = conn.execute(
                "SELECT * FROM shelf_items WHERE job_id=?", (int(row["job_id"]),)
            ).fetchone()
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return _shelf_item(got)

    def finish_shelf_item(
        self,
        job_id: int,
        *,
        ok: bool,
        file_count: int | None,
        total_bytes: int | None,
        reason_code: str | None,
        now: datetime,
        reason_args: dict[str, Any] | None = None,
    ) -> bool:
        """보관 한 건을 닫는다 — `copying` 일 때만(조건부, §15.2). 바뀌었으면 True.

        성공이면 `active` 이고 `shelved_at = last_used_at = now`. 잡이 이미 끝났으면 잡 상태와
        끝난 시각을 다시 복사한다(아직이면 나중에 `finish` 가 채운다). 잡 행이 이미 지워졌으면
        요청 때 복사한 값을 그대로 둔다. 실패면 `failed` + 이유 + `failed_at`. 잡과 묶음은 건드리지
        않는다 — 보관 실패가 잡을 실패로 만들지 않는다.
        """
        conn = self._conn()
        ts = _ts(now)
        conn.execute("BEGIN IMMEDIATE")
        try:
            if ok:
                cur = conn.execute(
                    "UPDATE shelf_items SET state=?, shelved_at=?, last_used_at=?, "
                    "stale_since=NULL, file_count=?, total_bytes=?, reason_code=NULL, "
                    "reason_args=NULL, error_code=NULL WHERE job_id=? AND state=?",
                    (
                        shelf_rules.STATE_ACTIVE,
                        ts,
                        ts,
                        file_count,
                        total_bytes,
                        int(job_id),
                        shelf_rules.STATE_COPYING,
                    ),
                )
                job = conn.execute(
                    "SELECT state, finished_at FROM jobs WHERE id=?", (int(job_id),)
                ).fetchone()
                if cur.rowcount == 1 and job is not None and job["state"] in TERMINAL_STATES:
                    conn.execute(
                        "UPDATE shelf_items SET job_state=?, job_finished_at=? WHERE job_id=?",
                        (job["state"], job["finished_at"], int(job_id)),
                    )
            else:
                cur = conn.execute(
                    "UPDATE shelf_items SET state=?, reason_code=?, reason_args=?, failed_at=? "
                    "WHERE job_id=? AND state=?",
                    (
                        shelf_rules.STATE_FAILED,
                        reason_code,
                        outcome.dump_args(reason_args),
                        ts,
                        int(job_id),
                        shelf_rules.STATE_COPYING,
                    ),
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return cur.rowcount == 1

    def touch_shelf_item(self, job_id: int, now: datetime) -> bool:
        """항목을 「썼다」. `should_touch` 가 그렇다고 할 때만 쓴다 — 썼으면 True(§4.3).

        보관 중은 1시간에 한 번, 정리 대기는 첫 쓰기에 바로 `active` 로 돌아간다. 동시에 둘이
        와도 쓰기 잠금 안에서 판정하므로 한 번만 쓴다.
        """
        conn = self._conn()
        ts = _ts(now)
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT state, last_used_at FROM shelf_items WHERE job_id=?", (int(job_id),)
            ).fetchone()
            if row is None or not shelf_rules.should_touch(row["last_used_at"], row["state"], ts):
                conn.execute("ROLLBACK")
                return False
            conn.execute(
                "UPDATE shelf_items SET state=?, last_used_at=?, stale_since=NULL WHERE job_id=?",
                (shelf_rules.STATE_ACTIVE, ts, int(job_id)),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return True

    def shelf_items(
        self,
        state: str | Iterable[str] | None = None,
        preset: str | None = None,
        limit: int | None = 200,
    ) -> list[ShelfItem]:
        """보관 항목들, 잡 번호가 큰 것부터. `state` 는 하나 또는 여럿, `limit=None` 은 전부."""
        where: list[str] = []
        params: list[Any] = []
        if state is not None:
            states = (state,) if isinstance(state, str) else tuple(state)
            where.append(f"state IN ({','.join('?' * len(states))})")
            params.extend(states)
        if preset is not None:
            where.append("preset=?")
            params.append(preset)
        sql = "SELECT * FROM shelf_items"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY job_id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        return [_shelf_item(r) for r in self._conn().execute(sql, tuple(params)).fetchall()]

    def shelf_item(self, job_id: int) -> ShelfItem | None:
        row = (
            self._conn()
            .execute("SELECT * FROM shelf_items WHERE job_id=?", (int(job_id),))
            .fetchone()
        )
        return None if row is None else _shelf_item(row)

    def latest_shelf_item(self, preset: str, branch: str | None) -> ShelfItem | None:
        """`/shelf/latest/<preset>?branch=` 의 대상 — 보관 중 · 정리 대기 가운데 잡 번호가 가장 큰
        것(§15.5). `branch` 가 None 이면 모든 브랜치에서."""
        params: list[Any] = [preset, *shelf_rules.SHELVED_STATES]
        sql = "SELECT * FROM shelf_items WHERE preset=? AND state IN (?,?)"
        if branch is not None:
            sql += " AND branch=?"
            params.append(branch)
        row = self._conn().execute(sql + " ORDER BY job_id DESC LIMIT 1", params).fetchone()
        return None if row is None else _shelf_item(row)

    def mark_shelf(
        self,
        job_id: int,
        state: str,
        now: datetime,
        *,
        only_from: Iterable[str] | None = None,
        if_last_used: float | None | object = _ANY,
        **fields: Any,
    ) -> bool:
        """행의 상태와 열을 바꾼다. 바뀌었으면 True. `only_from` 을 주면 그 상태에서만 바꾼다.

        `if_last_used` 를 주면 `last_used_at` 이 그 값일 때만 바꾼다(None 은 「비어 있을 때」).
        청소기가 계획을 읽은 뒤 누가 항목을 썼으면(되살리기) 그 계획은 낡았다 — 지우거나 정리
        대기로 옮기지 않는다(격리 검증 D1).

        필드 이름은 표의 열만 받는다(SQL 로 새지 않는다). 상태를 정하는 시각(`stale_since` ·
        `deleted_at` · `failed_at`)을 안 주면 `now` 로 채운다.
        """
        if state not in shelf_rules.SHELF_STATES:
            raise StoreError(f"unknown shelf state {state!r}")
        unknown = sorted(set(fields) - _SHELF_FIELDS)
        if unknown:
            raise StoreError(f"unknown shelf field(s): {', '.join(unknown)}")
        auto = {
            shelf_rules.STATE_STALE: "stale_since",
            shelf_rules.STATE_DELETED: "deleted_at",
            shelf_rules.STATE_FAILED: "failed_at",
        }.get(state)
        if auto is not None and auto not in fields:
            fields[auto] = _ts(now)
        if isinstance(fields.get("reason_args"), dict):
            fields["reason_args"] = outcome.dump_args(fields["reason_args"])
        sets = ["state=?", *(f"{k}=?" for k in fields)]
        params: list[Any] = [state, *fields.values(), int(job_id)]
        sql = f"UPDATE shelf_items SET {', '.join(sets)} WHERE job_id=?"
        if only_from is not None:
            allowed = tuple(only_from)
            sql += f" AND state IN ({','.join('?' * len(allowed))})"
            params.extend(allowed)
        if if_last_used is None:
            sql += " AND last_used_at IS NULL"
        elif if_last_used is not _ANY:
            sql += " AND last_used_at=?"
            params.append(if_last_used)
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            cur = conn.execute(sql, tuple(params))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return cur.rowcount == 1

    def forget_shelf(self, job_id: int) -> bool:
        """행을 지운다(30일 지난 묘비 · 실패 행). 폴더는 건드리지 않는다."""
        cur = self._conn().execute("DELETE FROM shelf_items WHERE job_id=?", (int(job_id),))
        return cur.rowcount == 1

    def shelf_totals(self) -> dict[str, Any]:
        """`{stored_bytes, items: {active, stale, pending, failed}}` — DB 열로 센다.

        `stored_bytes` 는 보관 중 · 정리 대기의 합이고, `pending` 은 `copying` 을 포함한다(§15.3).
        화면은 청소 회차 사이의 어긋남을 없애려고 `stage()` 로 다시 셀 수 있다(§15.5 — 서버 몫).
        """
        rows = (
            self._conn()
            .execute(
                "SELECT state, COUNT(*) AS n, COALESCE(SUM(total_bytes), 0) AS b "
                "FROM shelf_items GROUP BY state"
            )
            .fetchall()
        )
        by = {r["state"]: (int(r["n"]), int(r["b"] or 0)) for r in rows}
        count = {k: v[0] for k, v in by.items()}
        return {
            "stored_bytes": sum(by.get(s, (0, 0))[1] for s in shelf_rules.SHELVED_STATES),
            "items": {
                "active": count.get(shelf_rules.STATE_ACTIVE, 0),
                "stale": count.get(shelf_rules.STATE_STALE, 0),
                "pending": sum(count.get(s, 0) for s in shelf_rules.IN_FLIGHT_STATES),
                "failed": count.get(shelf_rules.STATE_FAILED, 0),
            },
        }

    def time_out_shelf_requests(self, now: datetime, *, grace_hours: float = 24) -> list[int]:
        """안전판(§15.2) — `pending` 인데 묶음의 만료 + `grace_hours` 가 지났으면 `failed(timeout)`.

        보관이 영원히 안 끝나도 묶음이 영원히 붙잡히지 않게 한다. 만료는 `ready_at +
        artifact_retention_hours` 다. `copying` 은 건드리지 않는다 — 지금 푸는 중이다.
        """
        cutoff = _ts(now) - float(grace_hours) * 3600
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            ids = [
                int(r["job_id"])
                for r in conn.execute(
                    "SELECT s.job_id FROM shelf_items s JOIN job_artifacts a "
                    "ON a.job_id = s.job_id WHERE s.state=? AND a.expires_at IS NOT NULL "
                    "AND a.expires_at <= ? ORDER BY s.job_id",
                    (shelf_rules.STATE_PENDING, cutoff),
                ).fetchall()
            ]
            if ids:
                marks = ",".join("?" * len(ids))
                conn.execute(
                    f"UPDATE shelf_items SET state=?, reason_code='timeout', failed_at=? "
                    f"WHERE job_id IN ({marks}) AND state=?",
                    (shelf_rules.STATE_FAILED, _ts(now), *ids, shelf_rules.STATE_PENDING),
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return ids

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
        """상태 전이 + 열 갱신 + 전이 이벤트를 한 번에(호출자가 트랜잭션을 연다).

        종료 상태로 가면 그 잡의 보관 행(M5p)에도 잡 상태와 끝난 시각을 같은 트랜잭션에서 적는다
        (§15.1). 잡을 끝내는 길은 `finish` 말고도 여럿이다(원격 워커 유실 · 대기 잡 취소 · 기동
        복구 · supersede). 전부 이 함수를 지나므로 여기 한 곳에 둔다 — 길마다 두면 하나를 빠뜨린다.
        """
        sets = ["state=?"] + [f"{k}=?" for k in cols]
        conn.execute(
            f"UPDATE jobs SET {', '.join(sets)} WHERE id=?", (state, *cols.values(), job_id)
        )
        if state in TERMINAL_STATES:
            conn.execute(
                "UPDATE shelf_items SET job_state=?, job_finished_at=? WHERE job_id=?",
                (state, cols.get("finished_at", at), job_id),
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
        link: JobLink | None = None,
        placement: str = PLACEMENT_POOL,
        display_pool: str | None = None,
        target_worker: str | None = None,
        concurrency_scope: str = SCOPE_POOL,
        sample: bool = True,
        fingerprint_hash: str | None = None,
        keep: bool = False,
        not_before: datetime | None = None,
        start_deadline: datetime | None = None,
        no_supersede: bool = False,
        retry: RetryPolicy | None = None,
        retry_disabled: bool = False,
    ) -> Job:
        """새 잡. tree 는 `uploading`, git_ref 는 바로 `queued`(예약이면 `scheduled`)로 만든다.

        `keep` 은 제출 때 얼린 값이다(프리셋 keep OR 제출 keep, M5p 결정 127)."""
        job, _ = self._create_job_tx(
            preset=preset,
            inputs=inputs,
            key=key,
            concurrency_group=concurrency_group,
            source=source,
            requester=requester,
            timeout_seconds=timeout_seconds,
            join_key=join_key,
            now=now,
            state=state,
            priority=priority,
            pool=pool,
            participant=None,
            link=link,
            placement=placement,
            display_pool=display_pool,
            target_worker=target_worker,
            concurrency_scope=concurrency_scope,
            sample=sample,
            fingerprint_hash=fingerprint_hash,
            keep=keep,
            not_before=not_before,
            start_deadline=start_deadline,
            no_supersede=no_supersede,
            retry=retry,
            retry_disabled=retry_disabled,
        )
        return job

    def create_job_with_submission(
        self, *, participant: str, **kwargs: Any
    ) -> tuple[Job, str, str]:
        """새 잡 + 요청자의 `cancel_job` capability 를 **한 트랜잭션**으로(M5l S8) —
        `(job, submission_id, cancel_token)`. capability 삽입이 실패하면 잡 행도 이벤트도 없다:
        `git_ref` 잡은 커밋되는 순간 돌 수 있는데 그 잡을 취소할 비밀이 없으면 안 된다."""
        job, issued = self._create_job_tx(participant=participant, **kwargs)
        assert issued is not None
        return job, issued[0], issued[1]

    def _create_job_tx(
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
        participant: str | None,
        state: str = UPLOADING,
        priority: int = 0,
        pool: str = DEFAULT_POOL,
        link: JobLink | None = None,
        placement: str = PLACEMENT_POOL,
        display_pool: str | None = None,
        target_worker: str | None = None,
        concurrency_scope: str = SCOPE_POOL,
        sample: bool = True,
        fingerprint_hash: str | None = None,
        keep: bool = False,
        not_before: datetime | None = None,
        start_deadline: datetime | None = None,
        no_supersede: bool = False,
        retry: RetryPolicy | None = None,
        retry_disabled: bool = False,
    ) -> tuple[Job, tuple[str, str] | None]:
        if state not in (UPLOADING, QUEUED, SCHEDULED):
            raise StoreError(f"new job cannot start in state {state}")
        if (state == SCHEDULED or not_before is not None) and (
            not_before is None or state == QUEUED
        ):
            # 예약은 시각이 있어야 하고(SCHEDULED), 시각이 있으면 곧장 queued 로 들어가지 않는다
            raise StoreError(f"a scheduled job needs not_before and cannot start {state}")
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
        issued: tuple[str, str] | None = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            if not_before is not None and join_key is not None:
                # 같은 합류 키의 예약이 이미 있나 — 판정과 삽입이 같은 트랜잭션이다(결정 209 ·
                # `_join_tx` 와 같은 방식). 업로드 중인 tree 예약도 센다: 두 세션이 같은 트리를 같이
                # 예약하면 둘째가 막혀야 한다. 도는 곳(풀 · 지정 머신)이 다르면 다른 예약이다 —
                # `--machine A` 와 `--machine B` 에 같은 sha 의 야간 gate 를 거는 것은 중복이
                # 아니다.
                dup = conn.execute(
                    "SELECT id FROM jobs WHERE join_key=? AND not_before IS NOT NULL "
                    "AND state IN (?, ?) AND pool=? AND target_worker IS ? ORDER BY id LIMIT 1",
                    (join_key, SCHEDULED, UPLOADING, pool, target_worker),
                ).fetchone()
                if dup is not None:
                    conn.execute("ROLLBACK")
                    raise ScheduleExists(int(dup["id"]))
            cur = conn.execute(
                "INSERT INTO jobs (preset, inputs_json, key, concurrency_group, source_json, "
                "requester_name, requester_label, state, created_at, queued_at, tree_hash, sha, "
                "timeout_seconds, join_key, priority, pool, link_url, link_label, placement, "
                "display_pool, target_worker, concurrency_scope, sample, fingerprint_hash, keep, "
                "not_before, start_deadline, no_supersede) "
                f"VALUES ({', '.join('?' * 28)})",
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
                    link.url if link else None,
                    link.label if link else None,
                    placement,
                    display_pool,
                    target_worker,
                    concurrency_scope or SCOPE_POOL,
                    1 if sample else 0,
                    fingerprint_hash,
                    1 if keep else 0,
                    _ts(not_before),
                    _ts(start_deadline),
                    1 if no_supersede else 0,
                ),
            )
            job_id = int(cur.lastrowid)
            if retry is not None:
                # 반복 정책을 얼린다(M5s 결정 229) — 끝날 때 프리셋을 다시 읽지 않는다. 사슬의
                # 뿌리는 자기 자신이다(결정 228). 같은 트랜잭션이라 정책 없는 반복 잡은 생기지
                # 않는다
                # 반복을 끈 잡(`retry: false`)은 사슬이 아니다 — 정책은 얼리되 뿌리는 없다
                conn.execute(
                    "UPDATE jobs SET chain_id=?, retry_on_exit=?, retry_interval_seconds=?, "
                    "retry_max_attempts=?, retry_disabled=? WHERE id=?",
                    (
                        None if retry_disabled else job_id,
                        int(retry.on_exit),
                        int(retry.interval_seconds),
                        int(retry.max_attempts),
                        1 if retry_disabled else 0,
                        job_id,
                    ),
                )
            self._event(conn, job_id, EVENT_STATE, {"state": state}, ts)
            if participant is not None:
                issued = self._insert_submission(conn, job_id, ROLE_CANCEL_JOB, participant, now)
            conn.execute("COMMIT")
        except ScheduleExists:
            raise
        except Exception:
            conn.execute("ROLLBACK")
            raise
        job = self.get_job(job_id)
        assert job is not None
        return job, issued

    def find_joinable(
        self, join_key: str, *, retry: RetryPolicy | None = None, retry_disabled: bool = False
    ) -> Job | None:
        marks = ",".join("?" * len(JOINABLE_STATES))
        rows = self._jobs(
            f"SELECT * FROM jobs WHERE join_key=? AND state IN ({marks}) "
            + _RETRY_JOIN_SQL
            + "AND not_before IS NULL ORDER BY id LIMIT 1",
            (join_key, *sorted(JOINABLE_STATES), *_frozen_retry(retry, retry_disabled)),
        )
        return rows[0] if rows else None

    def join_or_bump(
        self,
        join_key: str,
        name: str,
        label: str,
        priority: int,
        now: datetime,
        keep: bool = False,
        retry: RetryPolicy | None = None,
        retry_disabled: bool = False,
    ) -> Job | None:
        """합류 판정 + 합류자 기록 + 우선순위 상향을 **한 트랜잭션**으로(M5).

        요청자 본인이면 합류자를 안 넣는다. 우선순위는 `max(기존, 요청)` 로만 올라간다. `keep` 도
        같다 — 켤 수만 있고 끄지 않는다(M5p 결정 128).
        """
        got = self._join_tx(
            join_key,
            name,
            label,
            priority,
            now,
            with_submission=False,
            keep=keep,
            retry=retry,
            retry_disabled=retry_disabled,
        )
        return None if got is None else got[0]

    def join_or_bump_with_submission(
        self,
        join_key: str,
        name: str,
        label: str,
        priority: int,
        now: datetime,
        keep: bool = False,
        retry: RetryPolicy | None = None,
        retry_disabled: bool = False,
    ) -> tuple[Job, str, str] | None:
        """`join_or_bump` + 그 참여자의 `leave_submission` capability 를 같은 트랜잭션에(M5l S8)
        — `(job, submission_id, cancel_token)`, 합류할 잡이 없으면 None. 삽입이 실패하면
        joiner 행도 `join_count` 도 그대로다."""
        got = self._join_tx(
            join_key,
            name,
            label,
            priority,
            now,
            with_submission=True,
            keep=keep,
            retry=retry,
            retry_disabled=retry_disabled,
        )
        if got is None:
            return None
        job, issued = got
        assert issued is not None
        return job, issued[0], issued[1]

    def _join_tx(
        self,
        join_key: str,
        name: str,
        label: str,
        priority: int,
        now: datetime,
        *,
        with_submission: bool,
        keep: bool = False,
        retry: RetryPolicy | None = None,
        retry_disabled: bool = False,
    ) -> tuple[Job, tuple[str, str] | None] | None:
        # 취소 중인 잡에는 합류하지 않는다 — 결과를 줄 수 없는 잡이다. 거기 합류하면 합류자의
        # 대기가 남의 취소(또는 남의 새 잡에 밀린 것, M5o)로 종료 2 를 받는다(결정 113 위반).
        # 예약에서 온 잡(`not_before` 가 있다)에도 합류하지 않는다(M5s 결정 226) — 승격한 뒤에도
        # 그렇다. 붙으면 합류자가 그 마감을 물려받아 레인이 늦으면 `expired` 로 끝난다. 시작 마감이
        # 없는 예약도 같다: 반복의 다음 시도(결정 231)에 붙으면 합류자의 즉시 실행이 `interval`
        # 뒤로 밀리고, 그 사슬이 `exhausted` 로 끝나면 합류자도 1 을 받는다.
        # 얼린 반복 정책이 같은 잡에만 합류한다(#256 검증 P2) — 합류자는 그 잡의 정책을 받는다.
        # 반복을 원하는 제출이 `retry: false` 잡(또는 선언 전의 잡)에 붙으면 반복을 잃는다
        marks = ",".join("?" * len(JOINABLE_STATES))
        conn = self._conn()
        issued: tuple[str, str] | None = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT id, requester_name, priority, keep FROM jobs WHERE join_key=? "
                + _RETRY_JOIN_SQL
                + f"AND state IN ({marks}) AND not_before IS NULL ORDER BY id LIMIT 1",
                (join_key, *_frozen_retry(retry, retry_disabled), *sorted(JOINABLE_STATES)),
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
            if keep and not row["keep"]:
                self._turn_keep_on(conn, job_id, now)
            if with_submission:
                issued = self._insert_submission(conn, job_id, ROLE_LEAVE_SUBMISSION, name, now)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        job = self.get_job(job_id)
        assert job is not None
        return job, issued

    def set_priority(self, job_id: int, priority: int, now: datetime) -> bool:
        """대기 잡(uploading · queued)과 예약 잡(M5s)의 우선순위를 바꾼다. 아니면 False."""
        states = sorted(WAITING_STATES | SCHEDULED_STATES)
        marks = ",".join("?" * len(states))
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            cur = conn.execute(
                f"UPDATE jobs SET priority=? WHERE id=? AND state IN ({marks})",
                (int(priority), job_id, *states),
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
        # 홀드 알림 행(`hold:<규칙>`, M5t-b §4.12)은 잡 끝 알림 행이 아니다 — 세면 홀드 알림이
        # 먼저 간 잡의 잡 끝 알림을 기동 스캔이 영영 놓친다
        sent = (
            "SELECT job_id FROM notifications "
            "WHERE notify_name NOT LIKE 'hold:%'"  # mutcheck: hold-notify-blocks-job-scan
        )
        return self._jobs(
            f"SELECT * FROM jobs WHERE state IN ({marks}) AND finished_at >= ? "
            f"AND id NOT IN ({sent}) ORDER BY id",
            (*sorted(TERMINAL_STATES), _ts(since)),
        )

    def open_hold_jobs(self, since: datetime) -> list[int]:
        """since 이후에 만든 열린 홀드의 잡 id(만든 순서) — 홀드 알림의 기동 스캔용(§4.12)."""
        rows = self._conn().execute(
            "SELECT job_id FROM irreversible_holds WHERE released_at IS NULL AND created_at >= ? "
            "ORDER BY created_at, job_id",
            (_ts(since),),
        )
        return [int(r["job_id"]) for r in rows]

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

    def add_submission(
        self, job_id: int, role: str, now: datetime, *, participant: str
    ) -> tuple[str, str]:
        """`POST /jobs` 시도 하나에 capability 하나 — `(submission_id, cancel_token)`.

        비밀은 여기서 한 번 만들어 돌려주고 DB 에는 SHA-256 만 남는다(토큰과 같은 취급). 같은
        토큰 이름의 세션이 몇 번을 내든 행은 시도마다 다르다 — 참여자는 이름이 아니라 이 행이다.
        `participant` 는 비밀을 받은 토큰 이름 — `leave_submission` 은 그 이름만 쓸 수 있다.
        서버 경로는 잡 생성·합류와 같은 트랜잭션인 `create_job_with_submission` ·
        `join_or_bump_with_submission` 을 쓴다(M5l S8); 이것은 단독 삽입이다.
        """
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            issued = self._insert_submission(conn, job_id, role, participant, now)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return issued

    def _insert_submission(
        self, conn: sqlite3.Connection, job_id: int, role: str, participant: str, now: datetime
    ) -> tuple[str, str]:
        """열린 트랜잭션 안에서 행 하나 — `(submission_id, cancel_token)`."""
        if role not in (ROLE_CANCEL_JOB, ROLE_LEAVE_SUBMISSION):
            raise StoreError(f"unknown submission role {role!r}")
        submission_id = secrets.token_hex(8)
        cancel_token = secrets.token_urlsafe(32)
        # `-` 로 시작하는 비밀은 `rcm cancel N --cancel-token <비밀>` 에서 argparse 가 옵션으로
        # 읽는다(CI 에서 1/64 확률로 재현). 첫 글자가 `-` 면 다시 뽑는다 — 문서의 명령이 그대로
        # 되게 하는 쪽이 사용자에게 `--cancel-token=` 을 가르치는 쪽보다 낫다.
        while cancel_token.startswith("-"):
            cancel_token = secrets.token_urlsafe(32)
        conn.execute(
            "INSERT INTO submissions (job_id, submission_id, role, capability_hash, created_at, "
            "participant) VALUES (?, ?, ?, ?, ?, ?)",
            (job_id, submission_id, role, hash_token(cancel_token), _ts(now), participant),
        )
        return submission_id, cancel_token

    def submission_role(self, job_id: int, cancel_token: str) -> tuple[str, str, str] | None:
        """그 잡의 어느 capability 와 맞는가 — `(submission_id, role, participant)`, 아니면 None.

        비교는 해시끼리 `compare_digest` 로. 잡 번호가 다르면 비밀이 맞아도 None 이다 — 한 잡의
        비밀로 다른 잡을 건드릴 수 없다. role 은 저장된 그대로 돌려준다 — 화이트리스트 판정은
        부르는 쪽(서버)이 한다(M5l S10: 모르는 값이 `cancel_job` 로 승격되면 안 된다).
        """
        want = hash_token(cancel_token)
        rows = (
            self._conn()
            .execute(
                "SELECT submission_id, role, capability_hash, participant FROM submissions "
                "WHERE job_id=?",
                (job_id,),
            )
            .fetchall()
        )
        for r in rows:
            if hmac.compare_digest(str(r["capability_hash"]), want):
                return str(r["submission_id"]), str(r["role"]), str(r["participant"])
        return None

    def consume_leave(
        self,
        job_id: int,
        submission_id: str,
        participant: str,
        *,
        drop_joiner: bool,
        joiner_job_id: int | None = None,
    ) -> bool:
        """`leave_submission` 을 **한 번** 쓴다(M5l S6): 그 행이 아직 있고 · 그 잡의 것이고 ·
        `leave_submission` 이고 · 참여자 이름이 맞을 때만 지우고, 같은 트랜잭션에서 joiner 행
        (`drop_joiner`)도 뺀다. 아니면 False 이고 아무것도 바뀌지 않는다. `BEGIN IMMEDIATE`
        가 같은 비밀의 동시 요청을 줄 세우므로 둘 중 하나만 True 다."""
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            cur = conn.execute(
                "DELETE FROM submissions WHERE submission_id=? AND job_id=? AND role=? "
                "AND participant=?",
                (submission_id, job_id, ROLE_LEAVE_SUBMISSION, participant),
            )
            if cur.rowcount != 1:
                conn.execute("COMMIT")
                return False
            if drop_joiner:
                # 반복 사슬(M5s)이면 capability 는 뿌리에, 합류자 행은 머리 시도에 있다
                conn.execute(
                    "DELETE FROM joiners WHERE job_id=? AND name=?",
                    (joiner_job_id if joiner_job_id is not None else job_id, participant),
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return True

    def remove_submission(self, submission_id: str) -> bool:
        """행 하나를 지운다 — 그 비밀은 더 통하지 않는다. 서버의 leave 경로는 `consume_leave`
        (참여자 확인 · joiner 삭제와 한 트랜잭션)를 쓴다."""
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
        """uploading → queued. 예약 잡(`not_before` 가 있다)은 → scheduled 이고 `queued_at` 은
        승격할 때 정한다(M5s §4.1). 이미 취소됐으면 False."""
        conn = self._conn()
        ts = _ts(now)
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT state, source_json, not_before FROM jobs WHERE id=?", (job_id,)
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
            if row["not_before"] is not None:
                self._set_state(conn, job_id, SCHEDULED, ts)
            else:
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
        *,
        claimant: Claimant | None = None,
        now_fn: Callable[[], datetime] | None = None,
        session_id: str | None = None,
        claim_seq: int | None = None,
        grace_seconds: int | None = None,
    ) -> Job | None:
        """대기 잡 하나를 원자적으로 running 으로.

        M5t-b(결정 257 · 330 · 339-a): 같은 UPDATE 에서 `irreversible`(프리셋이 `self.irreversible`
        에 드나)과, 원격 claim 이면 세션 · `claim_seq` · 유예(`grace_seconds`)를 얼린다. 그룹이 NULL
        인 표식 프리셋 대기 잡(표식을 달기 전에 제출됨)은 그 유효 그룹 · 범위를 잡 행에 적고, 후보를
        고를 때의 그룹 배제도 그 유효 그룹으로 한다. 열린 그룹 홀드(`irreversible_holds`)의 그룹은
        범위 규칙대로 집지 않는다.

        시작 마감(`start_deadline`, M5s 결정 208)이 지난 잡은 집지 않는다 — 두 SQL 갈래 모두. 그
        잡을 `expired` 로 닫는 것은 주기 판정(`expire_overdue`)의 몫이다. `now_fn` 을 주면 마감과
        시작 시각을 **트랜잭션 안에서** 다시 읽는다. 호출자의 `now` 는 admit(부하 판정) 전에 받은
        것이라 그 사이에 마감이 지났을 수 있다.

        `claimant` 가 없으면 옛 규칙 그대로 — 그 풀의 잡, 그룹 배제는 **풀 안에서**(M5b).
        있으면 세 갈래(M5n §5.2): 그 워커를 지정한 잡 · 그 풀의 `pool` 잡(`gated` 제외) · 그 워커가
        eligible 인 `capable` 잡. 순서는 언제나 (우선순위 내림차순, id). 그룹 배제는 **후보 잡의
        범위**로(§6). `capable` 잡을 집으면 같은 UPDATE 에서 `pool` 을 집은 워커의 풀로 바꾸고
        대기 표시 풀은 `display_pool` 로 남긴다 — 끝난 잡은 「실제로 돈 곳」으로 기록된다.

        `worker_name` 이 있으면(원격 워커) 같은 트랜잭션에서 그 워커의 그 레인에 활성 잡이
        없는지 확인한다 — 있으면 `LaneBusy`(M5b-2, 레인 과할당 금지). 로컬 레인은 None.

        서버가 정지(`/pause`) 중이면 아무것도 집지 않고 None 이다. 호출자가 앞에서 `get_paused` 를
        봤더라도 같은 트랜잭션에서 다시 본다. 그 확인과 claim 사이에는 admit(부하 판정)이 끼어
        시간이 걸릴 수 있다. 그 틈에 걸린 정지를 놓치면 「정지 → 도는 잡 0 확인 → 재시작」 직후에
        잡이 시작돼 재시작으로 lost 가 된다. 정지 중에 집어야 하는 호출자는 없다.
        """
        if claimant is not None:
            pool = claimant.pool
            worker_name = None if claimant.worker == LOCAL_WORKER_ID else claimant.worker
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            ts = _ts(now_fn() if now_fn is not None else now)
            if conn.execute(_PAUSED_SQL).fetchone() is not None:
                conn.execute("COMMIT")
                return None
            if session_id is not None:
                # 같은 트랜잭션에서 세션을 다시 본다 — 펜싱된 세션이 집은 잡은 받을 사람이 없다
                fenced = conn.execute(
                    "SELECT fenced_at FROM worker_sessions WHERE id=?", (session_id,)
                ).fetchone()
                if fenced is not None and fenced["fenced_at"] is not None:  # mutcheck: claim-fenced
                    conn.execute("ROLLBACK")
                    raise SessionFenced(f"session {session_id[:8]} was fenced")
            busy = ",".join("?" * len(BUSY_STATES))
            busy_states = sorted(BUSY_STATES)
            if worker_name is not None:
                taken = conn.execute(
                    f"SELECT id FROM jobs WHERE worker_name=? AND lane=? AND state IN ({busy}) "
                    "ORDER BY id LIMIT 1",
                    (worker_name, lane, *busy_states),
                ).fetchone()
                if taken is not None:
                    conn.execute("ROLLBACK")
                    raise LaneBusy(f"lane {lane} already has job #{int(taken['id'])}")
            eff = self._effective_jobs_sql()
            if claimant is None:
                hold_sql, hold_params = _open_hold_sql(None, pool)
                # 도는 잡도 유효 그룹으로 센다(P6 GAP3) — 표식 전에 claim 된 그 프리셋의 잡은
                # 그룹이 NULL 이지만 같은 명령이다
                row = conn.execute(
                    f"SELECT c.id, c.preset, c.eg, c.es, c.concurrency_group AS g0 FROM {eff[0]} c "
                    "WHERE c.state=? AND c.pool=? "
                    "AND (c.start_deadline IS NULL OR c.start_deadline >= ?) "
                    f"AND (c.eg IS NULL OR NOT EXISTS (SELECT 1 FROM {eff[0]} b "
                    f"WHERE b.state IN ({busy}) AND b.eg = c.eg "  # mutcheck: busy-group-raw-pool
                    "AND (b.pool=? OR c.es=? OR b.es=?))) "
                    f"AND {hold_sql} ORDER BY c.priority DESC, c.id LIMIT 1",
                    (
                        *eff[1],
                        QUEUED,
                        pool,
                        ts,
                        *eff[1],
                        *busy_states,
                        pool,
                        SCOPE_GLOBAL,
                        SCOPE_GLOBAL,
                        *hold_params,
                    ),
                ).fetchone()
            else:
                row = self._claimable(conn, claimant, busy, busy_states, ts, eff)
            if row is None:
                conn.execute("COMMIT")
                return None
            job_id = int(row["id"])
            is_capable = claimant is not None and row["placement"] == PLACEMENT_CAPABLE
            # 같은 트랜잭션에서 센다 — 나중에는 알 수 없는 값이다(자기 포함).
            busy_now = conn.execute(
                f"SELECT count(*) AS n FROM jobs WHERE state IN ({busy}) AND pool=?",
                (*busy_states, pool),
            ).fetchone()["n"]
            sets = (
                "state=?, lane=?, started_at=?, phase=?, last_output_at=?, worker_name=?, "
                "concurrent_at_start=?"
            )
            params: list[Any] = [
                RUNNING,
                lane,
                ts,
                PHASE_MATERIALIZING,
                ts,
                worker_name,
                int(busy_now) + 1,
            ]
            if claimant is not None:
                sets += ", host_key=?, fingerprint_hash=?"
                params += [claimant.host_key, claimant.fingerprint_hash]
            if is_capable:
                # 대기 중 보이던 풀은 남기고, 잡은 「실제로 돈 풀」로 옮긴다(§5.2 · 뮤테이션 ⑥)
                sets += ", display_pool=COALESCE(display_pool, pool), pool=?"
                params.append(pool)
            # 얼림(M5t-b 결정 257): reload 나 재시작이 바꾼 값은 다음 claim 부터다
            sets += ", irreversible=?"
            params.append(1 if row["preset"] in self.irreversible else 0)
            if row["g0"] is None and row["eg"] is not None:
                # 표식 전에 제출된 잡의 유효 그룹 — 홀드도 이 값을 잡 행에서 읽는다(결정 339-d)
                sets += ", concurrency_group=?, concurrency_scope=?"
                params += [row["eg"], row["es"]]
            if worker_name is not None:
                sets += ", worker_session_id=?, claim_seq=?, disconnect_grace_seconds=?"
                params += [session_id, claim_seq, grace_seconds]
            cur = conn.execute(
                f"UPDATE jobs SET {sets} WHERE id=? AND state=?", (*params, job_id, QUEUED)
            )
            if cur.rowcount != 1:
                conn.execute("ROLLBACK")
                return None
            self._event(conn, job_id, EVENT_STATE, {"state": RUNNING}, ts)
            conn.execute("COMMIT")
        except (LaneBusy, SessionFenced):
            raise
        except Exception:
            conn.execute("ROLLBACK")
            raise
        try:
            return self.get_job(job_id)
        except Exception as e:
            # 커밋은 됐다 — 집힌 잡 id 를 예외에 실어 호출자가 그 잡을 닫게 한다. 모르면 잡이
            # `running` 으로 남고 재시작이 표식 잡의 그룹을 거짓으로 홀드한다(P3 3라운드 LOW)
            e.claimed_job_id = job_id  # type: ignore[attr-defined]  # mutcheck: claim-id-lost
            raise

    def _effective_jobs_sql(self) -> tuple[str, list[Any]]:
        """잡 표에 유효 그룹 `eg` · 범위 `es` 를 붙인 파생 표(M5t-b 결정 330 · RL48).

        그룹이 NULL 인 표식 프리셋 잡은 `self.irreversible` 의 (그룹, 범위)다. 그 밖에는 잡 행의 값
        그대로다. 표식 프리셋은 몇 개뿐이라 `CASE` 로 충분하다. SQLite 는 이 파생 표를 바깥 질의에
        펼쳐 넣으므로 색인을 그대로 쓴다."""
        irr = sorted(self.irreversible.items())
        if not irr:
            return (
                "(SELECT j.*, j.concurrency_group AS eg, j.concurrency_scope AS es FROM jobs j)",
                [],
            )
        whens = " ".join("WHEN ? THEN ?" for _ in irr)
        group_params = [x for name, (grp, _sc) in irr for x in (name, grp)]
        scope_params = [x for name, (_grp, sc) in irr for x in (name, sc)]
        sql = (
            f"(SELECT j.*, COALESCE(j.concurrency_group, CASE j.preset {whens} END) AS eg, "
            f"CASE WHEN j.concurrency_group IS NULL THEN CASE j.preset {whens} "
            "ELSE j.concurrency_scope END ELSE j.concurrency_scope END AS es FROM jobs j)"
        )
        return sql, [*group_params, *scope_params]

    @staticmethod
    def _claimable(
        conn: sqlite3.Connection,
        c: Claimant,
        busy: str,
        busy_states: list[str],
        ts: float,
        eff: tuple[str, list[Any]],
    ) -> sqlite3.Row | None:
        """M5n 의 후보 잡 하나 — 세 갈래 · (우선순위, id) · 후보 잡의 범위로 그룹 배제.

        `host` 범위에서 **호스트를 모르면 같은 호스트로 본다** — claim 하는 쪽(보고도 기억도 없는
        원격 워커)이든 도는 잡(호스트 없이 집힌 잡 · v23 이전 잡)이든. 모른다고 지나치면 같은
        컴퓨터의 두 워커가 시뮬레이터를 함께 잡는다(§12 fail-open 금지 · 엣지 검증)."""
        clauses = ["(target_worker = ?)"]
        params: list[Any] = [c.worker]
        gated = tuple(c.gated)
        gate_sql = f" AND preset NOT IN ({','.join('?' * len(gated))})" if gated else ""
        clauses.append(f"(target_worker IS NULL AND placement = ? AND pool = ?{gate_sql})")
        params += [PLACEMENT_POOL, c.pool, *gated]
        capable = tuple(c.capable) if c.accept_any else ()
        if capable:
            clauses.append(
                f"(target_worker IS NULL AND placement = ? AND preset IN "
                f"({','.join('?' * len(capable))}))"
            )
            params += [PLACEMENT_CAPABLE, *capable]
        known = tuple(c.known)
        if known:
            clauses.append(
                f"(target_worker IS NULL AND placement = ? AND preset NOT IN "
                f"({','.join('?' * len(known))}))"
            )
            params += [PLACEMENT_CAPABLE, *known]
        # 도는 잡도 유효 그룹으로 센다(P6 GAP3) — 표식 전에 claim 된 그룹 NULL 잡도 막는다
        group_sql = (
            f"(c.eg IS NULL OR NOT EXISTS (SELECT 1 FROM {eff[0]} b "
            f"WHERE b.state IN ({busy}) AND b.eg = c.eg AND ("  # mutcheck: busy-group-raw
            "c.es = ? "
            "OR (c.es = ? AND (? IS NULL OR b.host_key IS NULL OR b.host_key = ?)) "
            "OR (c.es NOT IN (?, ?) AND b.pool = ?))))"
        )
        params_group = [
            *eff[1],
            *busy_states,
            SCOPE_GLOBAL,
            SCOPE_HOST,
            c.host_key,
            c.host_key,
            SCOPE_GLOBAL,
            SCOPE_HOST,
            c.pool,
        ]
        # 후보 함수가 이 워커를 뺀 프리셋(`local_only`, M5t-a 결정 254) — 갈래 넷 모두에 건다.
        # 갈래마다 따로 달면 새 갈래가 생길 때 빠진다(5라운드의 HIGH 셋이 그런 경우였다)
        excluded = tuple(c.excluded)
        excluded_sql = f" AND c.preset NOT IN ({','.join('?' * len(excluded))})" if excluded else ""
        hold_sql, hold_params = _open_hold_sql(c.host_key, c.pool)
        sql = (
            "SELECT c.id, c.placement, c.preset, c.eg, c.es, c.concurrency_group AS g0 "
            f"FROM {eff[0]} c WHERE c.state = ? "
            "AND (c.start_deadline IS NULL OR c.start_deadline >= ?) AND ("
            + " OR ".join(clauses)
            + f"){excluded_sql} AND {group_sql} AND {hold_sql} "
            "ORDER BY c.priority DESC, c.id LIMIT 1"
        )
        return conn.execute(
            sql, (*eff[1], QUEUED, ts, *params, *excluded, *params_group, *hold_params)
        ).fetchone()

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
        """취소 요청. 대기 잡과 예약 잡은 즉시 cancelled, 실행 중이면 cancelling.
        종료 잡이면 None."""
        conn = self._conn()
        ts = _ts(now)
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                return None
            state = row["state"]
            if state in (UPLOADING, QUEUED, SCHEDULED):
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

    def supersede(
        self,
        new_id: int,
        old_ids: Iterable[int],
        now: datetime,
        *,
        mode: str,
        grace_seconds: float,
    ) -> list[int]:
        """새 잡 N 이 옛 잡들을 치운다 — **한 트랜잭션**(M5o §2.3 · §2.5). 처리한 id(오름차순).

        트랜잭션 **안에서** 새 잡과 후보를 다시 읽어 `core.supersede.superseded_by` 로 다시
        판정한다. 호출자의 후보 목록은 트랜잭션 밖에서 읽은 스냅샷이라 그새 합류자가 붙었거나
        사람이 취소했거나 다른 새 잡이 먼저 치웠을 수 있다(A17 · A18). `BEGIN IMMEDIATE` 가
        쓰는 쪽을 줄 세우므로 두 새 잡이 동시에 와도 서로를 치우는 일은 없다 — 규칙 7(id) 이
        방향을 정하고, 이미 끝난 새 잡은 아무것도 치우지 않는다.

        기다리는 잡은 바로 `cancelled`, 도는 잡은 `request_cancel` 과 같은 `cancelling`(SIGTERM ·
        유예 · 원격 워커는 heartbeat 응답) — 취소 주체는 `superseded`. 요약 칸은 도는 잡에도 지금
        써 둔다: N 을 기억할 자리가 거기뿐이고(DB 를 안 바꾼다), 끝날 때 `finish` 가 다시 쓴다.
        N 의 우선순위는 처리한 잡들과의 max 로만 오른다(결정 115).
        """
        wanted = {int(i) for i in old_ids}
        if not wanted:
            return []
        conn = self._conn()
        ts = _ts(now)
        text, code, args = outcome.summary(SUPERSEDED, by=int(new_id))
        taken: list[int] = []
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (new_id,)).fetchone()
            new = self._row_to_job(conn, row) if row is not None else None
            if new is None or new.state not in ACTIVE_STATES:
                conn.execute("ROLLBACK")
                return []
            marks = ",".join("?" * len(wanted))
            olds = [
                self._row_to_job(conn, r)
                for r in conn.execute(
                    f"SELECT * FROM jobs WHERE id IN ({marks}) ORDER BY id", tuple(sorted(wanted))
                ).fetchall()
            ]
            by_id = {j.id: j for j in olds}
            top = new.priority
            keep = new.keep
            for job_id in superseded_by(new, olds, mode):
                old = by_id[job_id]
                if old.state in WAITING_STATES:
                    self._set_state(
                        conn,
                        job_id,
                        CANCELLED,
                        ts,
                        finished_at=ts,
                        cancelled_by=SUPERSEDED,
                        summary=text,
                        summary_code=code,
                        summary_args=dump_args(args),
                    )
                else:  # RUNNING — `superseded_by` 가 mode 로 이미 걸렀다
                    self._set_state(
                        conn,
                        job_id,
                        CANCELLING,
                        ts,
                        cancel_requested_at=ts,
                        cancel_by=SUPERSEDED,
                        cancel_kill_at=ts + grace_seconds,
                        summary=text,
                        summary_code=code,
                        summary_args=dump_args(args),
                    )
                top = max(top, old.priority)
                keep = keep or old.keep
                taken.append(job_id)
            if top > new.priority:
                conn.execute("UPDATE jobs SET priority=? WHERE id=?", (int(top), new.id))
            if keep and not new.keep:
                # 치운 잡이 보관하려던 것을 새 잡이 이어받는다 — 우선순위를 넘기듯(결정 128)
                self._turn_keep_on(conn, new.id, now)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return taken

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
        hold_cause: str | None = None,
        log_bytes: int | None = None,
    ) -> bool:
        """종료 상태로. `only_from` 을 주면 그 상태에서만 바뀐다(경쟁 방지).

        `log_complete`(M5t-b §4.6)도 같은 트랜잭션에서 쓴다: 이미 0 으로 고정됐으면(T16) 0, 로컬
        레인 잡은 서버가 직접 쓰므로 참, 워커가 `log_bytes` 를 보냈으면 `log_offset ≥ log_bytes`,
        그 밖(레거시 · 유예 · 취소로 서버가 닫음)은 NULL(모름) 그대로다.

        `hold_cause` 는 프로세스 죽음 확인이 **없는** 닫힘이라는 뜻이다(M5t-b 결정 330). 표식 잡이면
        같은 트랜잭션에서 그 그룹의 홀드를 넣는다 — 닫힘과 홀드 사이에 claim 이 끼지 못한다(R15).

        `bundle`(`collect.CollectResult`)을 주면 산출물 묶음을 **같은 트랜잭션**에 쓴다 — 거절된
        finish 는 묶음도 안 남긴다(§5). 잡의 요약은 건드리지 않는다: 「테스트가 깨졌다」와
        「산출물을 못 올렸다」는 다른 사실이다(§3).

        `retried`(M5s 결정 225)는 **`running` 에서만** 간다 — 부르는 쪽이 무엇을 주든 여기서
        `only_from=(RUNNING,)` 이다. 그 사이 취소가 들어와 `cancelling` 이면 False 이고 다음 시도도
        없다(부르는 쪽이 `cancelled` 로 닫는다). 다음 시도는 **같은 트랜잭션**에서 `scheduled` 로
        만든다 — `retried_as` 없는 `retried` 는 생기지 않는다. 묶음은 받지 않는다: 보관은 사슬의
        마지막 시도에서만 한다(결정 228).
        """
        if state not in TERMINAL_STATES:
            raise StoreError(f"{state} is not a terminal state")
        if state == RETRIED:
            only_from = (RUNNING,)
            bundle = None
        conn = self._conn()
        ts = _ts(now)
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT state, cancel_by, worker_name, log_offset, log_complete "
                "FROM jobs WHERE id=?",
                (job_id,),
            ).fetchone()
            if row is None or row["state"] in TERMINAL_STATES:
                conn.execute("ROLLBACK")
                return False
            if only_from is not None and row["state"] not in set(only_from):
                conn.execute("ROLLBACK")
                return False
            if state == RETRIED:
                next_id, attempt, limit, at = self._next_attempt_tx(conn, job_id, ts)
                if summary is None:
                    summary, summary_code, summary_args = outcome.summary(
                        "retrying",
                        code=exit_code,
                        attempt=attempt,
                        max=limit,
                        by=next_id,
                        at=at.strftime("%Y-%m-%d %H:%MZ"),
                    )
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
                log_complete=_log_complete(row, log_bytes),
            )
            if hold_cause is not None:
                self._hold_tx(conn, job_id, hold_cause, ts)
            # 증거와 결과는 **같은 커밋**이다 — 거절된 finish 는 대장도 안 남긴다.
            for seq, name in enumerate(fail_names, start=1):
                conn.execute(
                    "INSERT OR IGNORE INTO job_failures(job_id, name, seq) VALUES (?,?,?)",
                    (job_id, name, seq),
                )
            # 원격 잡의 보관 행은 묶음이 ready 일 때(잡이 아직 running 일 때) 섰다 — 잡 상태와 끝난
            # 시각은 위의 `_set_state` 가 같은 트랜잭션에서 채웠다(M5p §15.1).
            if bundle is not None:
                _upsert_bundle(
                    conn,
                    job_id,
                    bundle,
                    now=now,
                    ttl_hours=ttl_hours,
                    shelf_enabled=self.shelf_enabled,
                    shelf_available=self.shelf_available,
                )
            conn.execute("COMMIT")
            return True
        except Exception:
            conn.execute("ROLLBACK")
            raise

    # ── 반복 사슬 (M5s §4.2) ────────────────────────────────────────────────

    #: 다음 시도로 넘기는 열(결정 228). 입력 · sha(소스) · 우선순위(합류로 오른 값 포함) · 링크 ·
    #: 배치 · 얼린 반복 정책 · supersede 끄기 · 사슬. 시각 · 레인 · 결과 · 취소 칸은 넘기지 않는다
    _CARRIED_COLUMNS = (
        "preset",
        "inputs_json",
        "key",
        "concurrency_group",
        "source_json",
        "requester_name",
        "requester_label",
        "tree_hash",
        "sha",
        "timeout_seconds",
        "join_key",
        "priority",
        "link_url",
        "link_label",
        "placement",
        "target_worker",
        "concurrency_scope",
        "sample",
        "keep",
        "no_supersede",
        "chain_id",
        "retry_on_exit",
        "retry_interval_seconds",
        "retry_max_attempts",
        "retry_disabled",
        "join_count",
    )

    def _next_attempt_tx(
        self, conn: sqlite3.Connection, job_id: int, ts: float
    ) -> tuple[int, int, int, datetime]:
        """열린 트랜잭션 안에서 — `job_id` 의 다음 시도를 `scheduled` 로 만든다(M5s 결정 211 · 228).

        `not_before = now + interval` 이고 **시작 마감은 없다**(결정 231) — 레인이나 그룹을 오래
        기다려도 `expired` 가 되지 않는다. 합류자 행은 다음 시도로 옮긴다(그들의 `rcm wait` 가
        사슬을 따라간다). 제출 capability 는 뿌리에 그대로 둔다 — 취소는 뿌리 id 로 검사한다.
        `(다음 id, 이 시도 번호, 상한, 다음 시각)` 을 돌려준다. 반복 정책이 없거나 상한에 닿았으면
        던진다 — 부르는 쪽(`outcome_for`)이 `exhausted` 로 골랐어야 할 잡이다.
        """
        cols = ", ".join(self._CARRIED_COLUMNS)
        row = conn.execute(
            f"SELECT {cols}, attempt, pool, display_pool, fingerprint_hash FROM jobs WHERE id=?",
            (job_id,),
        ).fetchone()
        if row is None or row["retry_on_exit"] is None or row["chain_id"] is None:
            raise StoreError(f"job #{job_id} has no retry policy — it cannot be retried")
        attempt = int(row["attempt"] or 1)
        limit = int(row["retry_max_attempts"] or 0)
        if attempt >= limit:
            raise StoreError(f"job #{job_id} is attempt {attempt} of {limit} — nothing to retry")
        at = ts + int(row["retry_interval_seconds"] or 0)
        values = [row[c] for c in self._CARRIED_COLUMNS]
        # 어디서든 도는 잡(`capable`)은 claim 이 실제로 돈 풀로 옮겨 둔다 — 다음 시도는 제출 때의
        # 풀(`display_pool`)에서 다시 기다린다. 집은 워커의 지문은 지정 머신 잡만 넘긴다(제출 때
        # 박힌 값이다)
        pool = row["display_pool"] if row["placement"] == PLACEMENT_CAPABLE else row["pool"]
        fingerprint = row["fingerprint_hash"] if row["target_worker"] is not None else None
        cur = conn.execute(
            f"INSERT INTO jobs ({cols}, state, created_at, not_before, retry_of, attempt, pool, "
            "display_pool, fingerprint_hash) "
            f"VALUES ({', '.join('?' * (len(values) + 8))})",
            (
                *values,
                SCHEDULED,
                ts,
                at,
                job_id,
                attempt + 1,
                pool or DEFAULT_POOL,
                row["display_pool"],
                fingerprint,
            ),
        )
        next_id = int(cur.lastrowid)
        conn.execute("UPDATE joiners SET job_id=? WHERE job_id=?", (next_id, job_id))
        # 이 시도의 보관 요청(원격 잡은 묶음이 ready 일 때 섰다)은 접는다 — 보관은 마지막 시도만
        # 한다(결정 228). 이미 복사를 시작했으면 그대로 둔다
        # 보관함이 꺼져 곧장 `failed`(disabled · unavailable)로 선 행도 접는다 — 시도마다 실패
        # 항목이 쌓인다. 마지막 시도가 자기 행을 세운다
        conn.execute(
            "DELETE FROM shelf_items WHERE job_id=? AND state IN ('pending', 'failed')", (job_id,)
        )
        # 원격 워커는 끝을 알리기 전에 묶음을 올린다(프로토콜은 그대로다, §4.2 「판정 자리」) — 그
        # 묶음은 바로 만료시켜 청소기가 다음 판에 치운다. 로컬 레인은 아예 모으지 않는다
        conn.execute(
            "UPDATE job_artifacts SET expires_at=? WHERE job_id=? AND state=? AND expires_at > ?",
            (ts, job_id, artifacts.READY, ts),
        )
        self._event(conn, next_id, EVENT_STATE, {"state": SCHEDULED}, ts)
        return next_id, attempt, limit, datetime.fromtimestamp(at, UTC)

    def chain_jobs(self, chain_id: int) -> list[Job]:
        """사슬의 시도 전부 — 시도 순(M5s). 사슬이 없는 잡이면 그 잡 하나(없으면 빈 목록)."""
        jobs = self._jobs(
            "SELECT * FROM jobs WHERE chain_id=? ORDER BY attempt, id", (int(chain_id),)
        )
        if jobs:
            return jobs
        one = self.get_job(int(chain_id))
        return [one] if one is not None else []

    def chain_head(self, job: Job) -> Job:
        """`job` 이 속한 사슬의 머리 — 끝에서 아직 안 끝난 시도, 다 끝났으면 마지막 시도(M5s 결정
        228). 사슬이 없는 잡은 자기 자신이다."""
        if job.chain_id is None:
            return job
        attempts = self.chain_jobs(job.chain_id)
        return attempts[-1] if attempts else job

    # ── 예약 (M5s §4.1 · §4.7) ─────────────────────────────────────────────

    def _expire_tx(self, conn: sqlite3.Connection, ts: float) -> list[int]:
        """열린 트랜잭션 안에서 — 시작 마감이 지난 예약 · 대기 잡을 `expired` 로. 닫은 id.

        조건부 UPDATE 다: 상태와 마감을 같은 문장이 본다. 도는 잡은 대상이 아니다(이미 시작했다).
        """
        rows = conn.execute(
            "SELECT id, start_deadline FROM jobs WHERE state IN (?, ?) "
            "AND start_deadline IS NOT NULL AND start_deadline < ? ORDER BY id",
            (SCHEDULED, QUEUED, ts),
        ).fetchall()
        out: list[int] = []
        for row in rows:
            deadline = datetime.fromtimestamp(float(row["start_deadline"]), UTC)
            text, code, args = outcome.summary(
                "start_deadline_passed", at=deadline.strftime("%Y-%m-%d %H:%MZ")
            )
            # `BEGIN IMMEDIATE` 가 쓰는 쪽을 줄 세우므로 위의 SELECT 가 본 상태 그대로다
            self._set_state(
                conn,
                int(row["id"]),
                EXPIRED,
                ts,
                finished_at=ts,
                summary=text,
                summary_code=code,
                summary_args=dump_args(args),
                lane=None,
                phase=None,
            )
            out.append(int(row["id"]))
        return out

    def _promote_tx(self, conn: sqlite3.Connection, ts: float) -> list[int]:
        """열린 트랜잭션 안에서 — 시각이 된 예약을 `queued` 로, `queued_at = now`. 올린 id.

        마감이 지난 예약은 올리지 않는다(같은 트랜잭션에서 `_expire_tx` 가 먼저 닫는다). 우선순위는
        제출 때 정해진 값 그대로다(결정 208).
        """
        ids = [
            int(r["id"])
            for r in conn.execute(
                "SELECT id FROM jobs WHERE state=? AND not_before IS NOT NULL AND not_before <= ? "
                "AND (start_deadline IS NULL OR start_deadline >= ?) ORDER BY not_before, id",
                (SCHEDULED, ts, ts),
            ).fetchall()
        ]
        out: list[int] = []
        for job_id in ids:
            cur = conn.execute(
                "UPDATE jobs SET queued_at=? WHERE id=? AND state=?", (ts, job_id, SCHEDULED)
            )
            if cur.rowcount != 1:
                continue
            self._set_state(conn, job_id, QUEUED, ts)
            out.append(job_id)
        return out

    def expire_overdue(self, now: datetime) -> list[int]:
        """시작 마감이 지난 예약 · 대기 잡을 `expired` 로 닫는다(5초 주기 판정). 닫은 id."""
        return self._schedule_tx(lambda conn, ts: self._expire_tx(conn, ts), now)

    def promote_due(self, now: datetime) -> list[int]:
        """시각이 된 예약을 `queued` 로 올린다(5초 주기 판정). 한 번만 오른다. 올린 id."""
        return self._schedule_tx(lambda conn, ts: self._promote_tx(conn, ts), now)

    def _schedule_tx(
        self, body: Callable[[sqlite3.Connection, float], list[int]], now: datetime
    ) -> list[int]:
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            out = body(conn, _ts(now))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return out

    def recover_on_start(self, now: datetime) -> tuple[list[int], list[int]]:
        """서버 시작 정리. 로컬 레인의 running·cancelling → lost, uploading → cancelled.

        원격 워커(`worker_name`)의 잡은 건드리지 않는다 — 워커는 살아 있을 수 있다. 그 세션을
        유예(`disconnect_grace_seconds`) 넘게 못 들으면 janitor(`mark_lost_workers`)가 잡마다
        `finish(…, hold_cause=…)` 로 닫는다(M5t-b §4.3 · 결정 330).

        예약(M5s §4.1)도 같은 트랜잭션에서 정리한다 — 워커가 뜨기 **전**이다. 서버가 꺼진 동안
        시작 마감을 넘긴 예약 · 대기 잡은 `expired` 로 닫고, 시각은 지났지만 마감 안인 예약은
        승격한다. 그 둘의 id 는 반환 모양을 바꾸지 않으려고 `schedule_recovered`
        (`(expired, promoted)`)에 남긴다.
        """
        conn = self._conn()
        ts = _ts(now)
        when = now.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%SZ")
        recover_state = LOST  # mutcheck: restart-lost — 조용히 succeeded/queued 로 되돌리지 않는다
        # 서버가 강제로 죽었으면 로컬 표식 잡의 명령은 감독자가 끊었을 수도, 아닐 수도 있다 — 확인할
        # 길이 없으므로 그 그룹을 홀드한다(M5t-b F76 · 결정 330)
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
                self._hold_tx(conn, int(row["id"]), "server_restarted", ts)
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
            expired = self._expire_tx(conn, ts)
            promoted = self._promote_tx(conn, ts)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        self.schedule_recovered = (expired, promoted)
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

    def actions_for(self, job_ids: Iterable[int]) -> dict[int, Action]:
        """잡마다 마지막 「사람이 할 일」 표식(M5s). 표식이 없는 잡은 결과에 없다.

        표식은 마커 이벤트라 잡이 끝나도 남는다 — 끝난 잡의 행(`/api/status` 의 최근 행 ·
        `GET /jobs/{id}` · 알림)이 이 길로 읽는다. 마커 전부를 읽지 않으려고 payload 머리로
        거른다. 두 쓰기 길(`_event` · `add_markers`)이 같은 `json.dumps(…, separators)` 를 쓴다.
        """
        ids = tuple(job_ids)
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        head = json.dumps({"kind": KIND_ACTION}, separators=(",", ":"))[:-1] + ",%"
        out: dict[int, Action] = {}
        for e in self._conn().execute(
            "SELECT job_id, at, payload FROM events "
            f"WHERE kind=? AND job_id IN ({marks}) AND payload LIKE ? ORDER BY id",
            (EVENT_MARKER, *ids, head),
        ):
            p = json.loads(e["payload"])
            found = action_of(Marker(at=_dt(e["at"]), kind=p["kind"], value=p["value"]))
            if found is not None:
                out[e["job_id"]] = found
        return out

    def chain_actions(self, jobs: Iterable[Job]) -> dict[int, Action]:
        """반복 사슬에 든 잡마다 사슬 전체의 표식(M5s 결정 233 · `progress.chain_action`).

        사슬이 없는 잡과 표식이 없는 사슬은 결과에 없다. 시도는 사슬마다 한 번에 읽고(사슬
        인덱스), 표식은 돈 시도들의 것만 한 번에 읽는다."""
        wanted = {j.id: j.chain_id for j in jobs if j.chain_id is not None}
        if not wanted:
            return {}
        chains = sorted(set(wanted.values()))
        marks = ",".join("?" * len(chains))
        rows = (
            self._conn()
            .execute(
                f"SELECT id, chain_id, state, started_at FROM jobs WHERE chain_id IN ({marks}) "
                "ORDER BY chain_id, attempt, id",
                tuple(chains),
            )
            .fetchall()
        )
        attempts: dict[int, list[tuple[int, bool, bool]]] = {}
        for r in rows:
            attempts.setdefault(int(r["chain_id"]), []).append(
                (int(r["id"]), r["started_at"] is not None, r["state"] in TERMINAL_STATES)
            )
        ran = [a[0] for seq in attempts.values() for a in seq if a[1]]
        actions = self.actions_for(ran)
        per_chain = {c: chain_action(seq, actions) for c, seq in attempts.items()}
        return {jid: a for jid, c in wanted.items() if (a := per_chain.get(c)) is not None}

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

    # ── 릴리스 드라이버 실행 대장 (스토어 탭 API 계약 v2) ──────────────────

    @staticmethod
    def _row_to_release(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "repo": row["repo"],
            "build_name": row["build_name"],
            "kind": row["kind"],
            "started_by": row["started_by"],
            "started_at": _dt(row["started_at"]),
            "pid": row["pid"],
            "log_path": row["log_path"],
            "exit_code": row["exit_code"],
            "finished_at": _dt(row["finished_at"]),
            "confirmed_n": row["confirmed_n"],
            "confirmed_by": row["confirmed_by"],
            "android_track": row["android_track"],
            "dry_run": bool(row["dry_run"]),
            "token_name": row["token_name"],
            "auto_n": bool(row["auto_n"]),
        }

    def create_release(
        self,
        *,
        repo: str,
        build_name: str,
        kind: str,
        started_by: str,
        now: datetime,
        log_path: str,
        confirmed_n: int | None = None,
        confirmed_by: str | None = None,
        android_track: str | None = None,
        dry_run: bool = False,
        auto_n: bool = False,
    ) -> int:
        """실행 행 하나를 연다(pid · 토큰 이름은 프로세스를 띄운 뒤 `set_release_started` 로)."""
        cur = self._conn().execute(
            "INSERT INTO releases (repo, build_name, kind, started_by, started_at, log_path, "
            "confirmed_n, confirmed_by, android_track, dry_run, auto_n) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                repo,
                build_name,
                kind,
                started_by,
                _ts(now),
                log_path,
                confirmed_n,
                confirmed_by,
                android_track,
                1 if dry_run else 0,
                1 if auto_n else 0,
            ),
        )
        return int(cur.lastrowid or 0)

    def set_release_started(self, release_id: int, *, pid: int, token_name: str) -> None:
        self._conn().execute(
            "UPDATE releases SET pid=?, token_name=? WHERE id=?", (int(pid), token_name, release_id)
        )

    def finish_release(self, release_id: int, exit_code: int | None, now: datetime) -> bool:
        """끝났다고 적는다 — 한 번만(이미 끝난 행은 False). exit_code None = 모른다."""
        cur = self._conn().execute(
            "UPDATE releases SET exit_code=?, finished_at=? WHERE id=? AND finished_at IS NULL",
            (exit_code, _ts(now), release_id),
        )
        return cur.rowcount == 1

    def get_release(self, release_id: int) -> dict[str, Any] | None:
        row = self._conn().execute("SELECT * FROM releases WHERE id=?", (release_id,)).fetchone()
        return self._row_to_release(row) if row else None

    def latest_release(self, repo: str, build_name: str | None = None) -> dict[str, Any] | None:
        """저장소(그리고 build_name)의 가장 최근 실행 행."""
        if build_name is None:
            row = (
                self._conn()
                .execute("SELECT * FROM releases WHERE repo=? ORDER BY id DESC LIMIT 1", (repo,))
                .fetchone()
            )
        else:
            row = (
                self._conn()
                .execute(
                    "SELECT * FROM releases WHERE repo=? AND build_name=? ORDER BY id DESC LIMIT 1",
                    (repo, build_name),
                )
                .fetchone()
            )
        return self._row_to_release(row) if row else None

    def list_open_releases(self, repo: str | None = None) -> list[dict[str, Any]]:
        """아직 끝났다고 적히지 않은 실행들(pid 생존 확인은 호출자가)."""
        if repo is None:
            rows = (
                self._conn()
                .execute("SELECT * FROM releases WHERE finished_at IS NULL ORDER BY id")
                .fetchall()
            )
        else:
            rows = (
                self._conn()
                .execute(
                    "SELECT * FROM releases WHERE repo=? AND finished_at IS NULL ORDER BY id",
                    (repo,),
                )
                .fetchall()
            )
        return [self._row_to_release(r) for r in rows]

    def count_releases(self) -> int:
        return int(self._conn().execute("SELECT COUNT(*) FROM releases").fetchone()[0])

    # ── 스토어 버전 드래프트 대장 (버전 페이지 · v20) ───────────────────────

    @staticmethod
    def _row_to_version(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "repo": row["repo"],
            "ios_version": row["ios_version"],
            "android_version": row["android_version"],
            "state": row["state"],
            "created_by": row["created_by"],
            "created_at": _dt(row["created_at"]),
            "last_edit_at": _dt(row["last_edit_at"]),
            "expires_at": _dt(row["expires_at"]),
            "asc_version_id": row["asc_version_id"],
            "prefill_json": row["prefill_json"],
            "edited_json": row["edited_json"],
            "error": row["error"],
            "create_job_id": row["create_job_id"],
            "delete_job_id": row["delete_job_id"],
            "release_id": row["release_id"],
            "review_job_id": row["review_job_id"],
            "upload_job_id": row["upload_job_id"],
            "expiry_warned": bool(row["expiry_warned"]),
        }

    def create_version(
        self,
        *,
        repo: str,
        ios_version: str | None,
        android_version: str | None,
        state: str,
        created_by: str,
        now: datetime,
        expires_at: datetime,
        create_job_id: int | None = None,
    ) -> int:
        """드래프트 행 하나. 이름은 둘 중 하나 이상(앱 레벨 검사 — 여기서는 `ValueError`)."""
        if not ios_version and not android_version:
            raise ValueError("a version needs ios_version or android_version")
        if state not in VERSION_STATES:
            raise ValueError(f"unknown version state {state!r}")
        cur = self._conn().execute(
            "INSERT INTO versions (repo, ios_version, android_version, state, created_by, "
            "created_at, expires_at, create_job_id) VALUES (?,?,?,?,?,?,?,?)",
            (
                repo,
                ios_version or None,
                android_version or None,
                state,
                created_by,
                _ts(now),
                _ts(expires_at),
                create_job_id,
            ),
        )
        return int(cur.lastrowid or 0)

    def get_version(self, version_id: int) -> dict[str, Any] | None:
        row = (
            self._conn().execute("SELECT * FROM versions WHERE id=?", (int(version_id),)).fetchone()
        )
        return self._row_to_version(row) if row else None

    def list_versions(
        self, repo: str | None = None, *, include_closed: bool = True
    ) -> list[dict[str, Any]]:
        """저장소의 드래프트들, 새것부터. `include_closed=False` 면 submitted · discarded 를 뺀다.
        `repo=None` 은 전부(기동 복구 · 청소기)."""
        sql = "SELECT * FROM versions"
        where: list[str] = []
        params: list[Any] = []
        if repo is not None:
            where.append("repo=?")
            params.append(repo)
        if not include_closed:
            marks = ",".join("?" * len(VERSION_CLOSED_STATES))
            where.append(f"state NOT IN ({marks})")
            params.extend(VERSION_CLOSED_STATES)
        if where:
            sql += " WHERE " + " AND ".join(where)
        rows = self._conn().execute(sql + " ORDER BY id DESC", tuple(params)).fetchall()
        return [self._row_to_version(r) for r in rows]

    def update_version(self, version_id: int, **fields: Any) -> bool:
        """허용 열만 고친다(`VERSION_UPDATABLE`). 모르는 열은 `ValueError` — 오타가 조용히 무시되지
        않게. 시각 열은 datetime, `expiry_warned` 는 bool 로 받는다. 행이 없으면 False."""
        if not fields:
            return self.get_version(version_id) is not None
        unknown = sorted(set(fields) - VERSION_UPDATABLE)
        if unknown:
            raise ValueError(f"versions: unknown column(s): {', '.join(unknown)}")
        if "state" in fields and fields["state"] not in VERSION_STATES:
            raise ValueError(f"unknown version state {fields['state']!r}")
        sets: list[str] = []
        params: list[Any] = []
        for key, value in fields.items():
            if key in ("last_edit_at", "expires_at"):
                value = _ts(value)
            elif key == "expiry_warned":
                value = 1 if value else 0
            sets.append(f"{key}=?")
            params.append(value)
        params.append(int(version_id))
        cur = self._conn().execute(
            f"UPDATE versions SET {', '.join(sets)} WHERE id=?", tuple(params)
        )
        return cur.rowcount == 1

    def version_for_job(self, job_id: int) -> dict[str, Any] | None:
        """이 잡을 만들기 · 지우기 · 심사 · 올리기로 연결한 행(완료 훅). 없으면 None."""
        row = (
            self._conn()
            .execute(
                "SELECT * FROM versions WHERE create_job_id=? OR delete_job_id=? "
                "OR review_job_id=? OR upload_job_id=? ORDER BY id DESC LIMIT 1",
                (int(job_id), int(job_id), int(job_id), int(job_id)),
            )
            .fetchone()
        )
        return self._row_to_version(row) if row else None

    def version_for_release(self, release_id: int) -> dict[str, Any] | None:
        """드라이버 회차(`releases.id`)가 붙은, 도는 중인 행."""
        row = (
            self._conn()
            .execute(
                "SELECT * FROM versions WHERE release_id=? AND state=? ORDER BY id DESC LIMIT 1",
                (int(release_id), VERSION_RUNNING),
            )
            .fetchone()
        )
        return self._row_to_version(row) if row else None

    def open_versions_expired(self, now: datetime) -> list[dict[str, Any]]:
        """편집한 적 없는(`last_edit_at` NULL) `editing` · `failed` 드래프트 중 만료가 지난 것 —
        청소기가 «버리기» 경로로 지운다(워크플랜 Q1 · §14-3).

        `failed`(만들기 잡이 실패한 행)가 여기 드는 것은, 그 행이 이름을 붙잡고 있는데 아무 조회에도
        안 걸려 영원히 남기 때문이다. 그 행에는 App Store 버전 id 가 없다 — 서버는 만들기가
        **성공했을 때만** 적는다 — 그러니 스토어 삭제 잡은 나가지 않고 그 자리에서 `discarded` 다.
        """
        marks = ",".join("?" * len(VERSION_SWEEPABLE_STATES))
        rows = (
            self._conn()
            .execute(
                f"SELECT * FROM versions WHERE state IN ({marks}) AND last_edit_at IS NULL "
                "AND expires_at<? ORDER BY id",
                (*VERSION_SWEEPABLE_STATES, _ts(now)),
            )
            .fetchall()
        )
        return [self._row_to_version(r) for r in rows]

    def versions_to_warn(self, now: datetime) -> list[dict[str, Any]]:
        """편집이 있는 열린 드래프트 중 만료가 지났는데 아직 경고하지 않은 것(Q2 — 지우지 않고
        `expiry_warned` 만 켠다)."""
        marks = ",".join("?" * len(VERSION_CLOSED_STATES))
        rows = (
            self._conn()
            .execute(
                "SELECT * FROM versions WHERE last_edit_at IS NOT NULL AND expires_at<? "
                f"AND expiry_warned=0 AND state NOT IN ({marks}) ORDER BY id",
                (_ts(now), *VERSION_CLOSED_STATES),
            )
            .fetchall()
        )
        return [self._row_to_version(r) for r in rows]

    def count_versions(self) -> int:
        return int(self._conn().execute("SELECT COUNT(*) FROM versions").fetchone()[0])

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

    # ── 머신의 능력 (M5n §4 · §11.1) ─────────────────────────────────────────

    @staticmethod
    def _machine_row(r: sqlite3.Row, *, changed: bool = False) -> MachineRow:
        return MachineRow(
            worker=r["worker"],
            host_key=r["host_key"],
            display_name=r["display_name"],
            accept_any=bool(r["accept_any"]),
            fingerprint_hash=r["fingerprint_hash"],
            fingerprint_json=r["fingerprint_json"],
            previous_json=r["previous_json"],
            changed_at=_dt(r["changed_at"]),
            measured_at=_dt(r["measured_at"]),
            generation=r["generation"],
            report_json=r["report_json"],
            changed=changed,
        )

    def upsert_machine(
        self,
        worker: str,
        *,
        host_key: str | None,
        display_name: str | None,
        accept_any: bool,
        fingerprint_hash: str | None,
        fingerprint_json: str | None,
        measured_at: datetime,
        generation: str | None,
        report_json: str | None,
    ) -> MachineRow:
        """워커의 마지막 보고를 덮어 쓴다. **앞 지문과 다르면** `previous_json` · `changed_at` 을
        갱신하고 돌려주는 행의 `changed` 가 True 다.

        앞 지문이 없으면(첫 보고) 바뀐 것이 아니다. 새 지문이 None(모름)이면 비교하지 않고 앞
        지문을 그대로 둔다 — 「모름」을 「바뀜」으로도 「그대로」로도 꾸미지 않는다(결정 90).
        """
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            old = conn.execute("SELECT * FROM machines WHERE worker=?", (worker,)).fetchone()
            changed = (
                old is not None
                and old["fingerprint_hash"] is not None
                and fingerprint_hash is not None
                and old["fingerprint_hash"] != fingerprint_hash
            )
            if old is None:
                conn.execute(
                    "INSERT INTO machines (worker, host_key, display_name, accept_any, "
                    "fingerprint_hash, fingerprint_json, previous_json, changed_at, measured_at, "
                    "generation, report_json) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?)",
                    (
                        worker,
                        host_key,
                        display_name,
                        1 if accept_any else 0,
                        fingerprint_hash,
                        fingerprint_json,
                        _ts(measured_at),
                        generation,
                        report_json,
                    ),
                )
            else:
                keep_fp = fingerprint_hash is None and old["fingerprint_hash"] is not None
                conn.execute(
                    "UPDATE machines SET host_key=?, display_name=?, accept_any=?, "
                    "fingerprint_hash=?, fingerprint_json=?, previous_json=?, changed_at=?, "
                    "measured_at=?, generation=?, report_json=? WHERE worker=?",
                    (
                        host_key,
                        display_name,
                        1 if accept_any else 0,
                        old["fingerprint_hash"] if keep_fp else fingerprint_hash,
                        old["fingerprint_json"] if keep_fp else fingerprint_json,
                        old["fingerprint_json"] if changed else old["previous_json"],
                        _ts(measured_at) if changed else old["changed_at"],
                        _ts(measured_at),
                        generation,
                        report_json,
                        worker,
                    ),
                )
            row = conn.execute("SELECT * FROM machines WHERE worker=?", (worker,)).fetchone()
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return self._machine_row(row, changed=changed)

    def get_machine(self, worker: str) -> MachineRow | None:
        r = self._conn().execute("SELECT * FROM machines WHERE worker=?", (worker,)).fetchone()
        return self._machine_row(r) if r else None

    def list_machines(self) -> list[MachineRow]:
        rows = self._conn().execute("SELECT * FROM machines ORDER BY worker").fetchall()
        return [self._machine_row(r) for r in rows]

    def record_fingerprint(self, hash: str, json_text: str, now: datetime) -> None:
        """잡 상세의 「이 잡이 돈 환경」. 같은 해시는 처음 본 시각을 지킨다."""
        self._conn().execute(
            "INSERT OR IGNORE INTO fingerprints (hash, json, first_seen_at) VALUES (?, ?, ?)",
            (hash, json_text, _ts(now)),
        )

    def get_fingerprint(self, hash: str) -> str | None:
        r = self._conn().execute("SELECT json FROM fingerprints WHERE hash=?", (hash,)).fetchone()
        return r["json"] if r else None

    def check_records(
        self, worker: str, presets: Sequence[str], *, fingerprint_hash: str | None = None
    ) -> list[CheckRecord]:
        """그 워커에서 돌았거나 그 워커를 지정한 점검 프리셋 잡들(§4.4). 로컬은 `worker_name`
        NULL 인 잡이다. 지정 잡은 제출 때 대상의 지문을 미리 박아 두므로 기다리는 동안에도
        「점검 중」으로 보인다.

        `fingerprint_hash` 를 주면 그 지문의 기록만 — 판정(`check_state`)은 어차피 현재 지문의
        기록만 본다. **`jobs_check(worker_name, preset, fingerprint_hash, id)` 를 탄다.** 옛 꼴
        (`target_worker = ? OR worker_name = ?`)은 인덱스를 못 타 `jobs` 를 통째로 훑었고, 이 읽기는
        `/api/status` · claim 마다 머신 수만큼 돈다 — 보존된 잡 수에 끌려가는 M5f 결정 49 의 함정
        이다(엣지 검증 실측: 완료 잡 5만에서 status 6.7 ms → 628 ms). 지정 잡은 집히면
        `worker_name` 이 그 워커가 되므로(로컬은 NULL 그대로) 「아직 안 집힌 지정 잡」만 따로 읽으면
        같은 집합이다."""
        names = tuple(presets)
        if not names:
            return []
        marks = ",".join("?" * len(names))
        fp_sql = " AND fingerprint_hash = ?" if fingerprint_hash is not None else ""
        fp_params: tuple[Any, ...] = (fingerprint_hash,) if fingerprint_hash is not None else ()
        cols = "SELECT id, preset, state, fingerprint_hash FROM jobs"
        shown = "(state != ? OR target_worker IS NOT NULL)"
        if worker == LOCAL_WORKER_ID:
            sql = (
                f"{cols} WHERE worker_name IS NULL AND preset IN ({marks}){fp_sql} "
                f"AND (target_worker IS NULL OR target_worker = ?) AND {shown} ORDER BY id"
            )
            params: tuple[Any, ...] = (*names, *fp_params, worker, QUEUED)
        else:
            sql = (
                f"{cols} WHERE worker_name = ? AND preset IN ({marks}){fp_sql} AND {shown} "
                f"UNION ALL {cols} WHERE worker_name IS NULL AND preset IN ({marks}){fp_sql} "
                f"AND target_worker = ? AND {shown} ORDER BY id"
            )
            params = (
                worker,
                *names,
                *fp_params,
                QUEUED,
                *names,
                *fp_params,
                worker,
                QUEUED,
            )
        rows = self._conn().execute(sql, params).fetchall()
        return [
            CheckRecord(
                preset=r["preset"],
                job_id=int(r["id"]),
                job_state=r["state"],
                fingerprint_hash=r["fingerprint_hash"],
            )
            for r in rows
        ]

    def auto_check_done(self, worker: str, preset: str, fingerprint_hash: str) -> bool:
        """그 (워커, 점검, 지문)에 rcm 이 스스로 건 적이 있나 — 한 번 제한(결정 95)."""
        r = (
            self._conn()
            .execute(
                "SELECT 1 FROM machine_checks WHERE worker=? AND preset=? AND fingerprint_hash=? "
                "AND auto=1 LIMIT 1",
                (worker, preset, fingerprint_hash),
            )
            .fetchone()
        )
        return r is not None

    def auto_checks_since(self, worker: str, preset: str, since: datetime) -> int:
        """그 (워커, 점검)에 rcm 이 `since` 뒤로 스스로 건 횟수 — 하루 상한(결정 105)."""
        r = (
            self._conn()
            .execute(
                "SELECT count(*) AS n FROM machine_checks WHERE worker=? AND preset=? AND auto=1 "
                "AND submitted_at > ?",
                (worker, preset, _ts(since)),
            )
            .fetchone()
        )
        return int(r["n"])

    def check_was_automatic(self, job_id: int) -> bool:
        """그 점검 잡을 rcm 이 스스로 걸었나(사람의 다시 점검 · 직접 제출이면 False)."""
        r = (
            self._conn()
            .execute("SELECT auto FROM machine_checks WHERE job_id=? LIMIT 1", (job_id,))
            .fetchone()
        )
        return bool(r and r["auto"])

    def record_machine_check(
        self,
        worker: str,
        preset: str,
        fingerprint_hash: str,
        job_id: int | None,
        now: datetime,
        *,
        auto: bool,
    ) -> None:
        self._conn().execute(
            "INSERT INTO machine_checks (worker, preset, fingerprint_hash, job_id, "
            "submitted_at, auto) VALUES (?, ?, ?, ?, ?, ?)",
            (worker, preset, fingerprint_hash, job_id, _ts(now), 1 if auto else 0),
        )

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
            # 예약 잡도 「기다리는 잡」이다(M5s) — 14일 뒤 예약이 있는 풀의 워커를 잊으면
            # 경보가 꺼진다
            pending = sorted(WAITING_STATES | SCHEDULED_STATES)
            waiting = ",".join("?" * len(pending))
            rows = conn.execute(
                f"SELECT name FROM workers WHERE last_seen_at < ? AND name NOT IN "
                f"(SELECT worker_name FROM jobs WHERE worker_name IS NOT NULL "
                f"AND state IN ({busy})) "
                # 그 풀에 아직 기다리는 잡이 있으면 「은퇴」가 아니라 「일주일째 고장」이다.
                # 지우면 `pools_without_workers` 가 비어 `rcm check` 의 경보가 꺼진다 —
                # 잡은 그대로 멈춰 있는데.
                f"AND pool NOT IN (SELECT DISTINCT pool FROM jobs WHERE state IN ({waiting})) "
                # 열린 홀드가 있는 워커도 남긴다(P6 GAP5) — 잊으면 세션 행이 지워져 돌아온 워커의
                # `previous_sessions` 가 그 홀드를 풀지 못한다
                "AND name NOT IN (SELECT j.worker_name FROM jobs j JOIN irreversible_holds h "
                "ON h.job_id = j.id WHERE h.released_at IS NULL "  # mutcheck: forget-held-worker
                "AND j.worker_name IS NOT NULL) "
                "ORDER BY name",
                (_ts(cutoff), *sorted(BUSY_STATES), *pending),
            ).fetchall()
            gone = [r["name"] for r in rows]
            if gone:
                marks = ",".join("?" * len(gone))
                conn.execute(f"DELETE FROM workers WHERE name IN ({marks})", tuple(gone))
                # 세션 행은 워커 행과 함께만 지운다(M5t-b 결정 265, F21)
                conn.execute(
                    f"DELETE FROM worker_sessions WHERE worker_name IN ({marks})", tuple(gone)
                )
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

    # ── 워커 세션 (M5t-b 결정 255 · 265 · 336) ────────────────────────────────

    def get_session(self, session_id: str) -> SessionRow | None:
        row = (
            self._conn()
            .execute("SELECT * FROM worker_sessions WHERE id=?", (session_id,))
            .fetchone()
        )
        if row is None:
            return None
        return SessionRow(
            id=row["id"],
            worker_name=row["worker_name"],
            registered_at=_dt(row["registered_at"]),
            fenced_at=_dt(row["fenced_at"]),
            fenced_by=row["fenced_by"],
            orphan_unverified=bool(row["orphan_unverified"]),
        )

    def session_names(self, session_ids: Iterable[str]) -> dict[str, str]:
        """세션 id → 워커 이름(표에 있는 것만)."""
        ids = sorted(set(session_ids))
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        return {
            r["id"]: r["worker_name"]
            for r in self._conn().execute(
                f"SELECT id, worker_name FROM worker_sessions WHERE id IN ({marks})", ids
            )
        }

    def has_legacy_busy(self, name: str) -> bool:
        """그 이름에 세션 없이 claim 된(v26 이전) busy 잡이 있나 — 옛 워커의 요청을 받는
        조건이다(T11)."""
        busy = ",".join("?" * len(BUSY_STATES))
        return (
            self._conn()
            .execute(
                f"SELECT 1 FROM jobs WHERE worker_name=? AND worker_session_id IS NULL "
                f"AND state IN ({busy}) LIMIT 1",
                (name, *sorted(BUSY_STATES)),
            )
            .fetchone()
            is not None
        )

    def jobs_of_session(self, session_id: str) -> list[Job]:
        """그 세션이 claim 해서 아직 busy 인 잡(레인 순)."""
        busy = ",".join("?" * len(BUSY_STATES))
        return self._jobs(
            f"SELECT * FROM jobs WHERE worker_session_id=? AND state IN ({busy}) ORDER BY lane, id",
            (session_id, *sorted(BUSY_STATES)),
        )

    def busy_remote_jobs(self) -> list[Job]:
        """원격 워커가 claim 해서 아직 busy 인 잡 전부(id 순) — janitor 의 유예 판정 후보."""
        busy = ",".join("?" * len(BUSY_STATES))
        return self._jobs(
            f"SELECT * FROM jobs WHERE worker_name IS NOT NULL AND state IN ({busy}) ORDER BY id",
            tuple(sorted(BUSY_STATES)),
        )

    def current_session(self, name: str) -> str | None:
        """그 이름의 현재 세션(펜싱되지 않은 행). 등록 트랜잭션이 많아야 하나로 지킨다."""
        row = (
            self._conn()
            .execute(
                "SELECT id FROM worker_sessions WHERE worker_name=? AND fenced_at IS NULL "
                "ORDER BY registered_at DESC LIMIT 1",
                (name,),
            )
            .fetchone()
        )
        return row["id"] if row else None

    def legacy_jobs_of_worker(self, name: str) -> list[Job]:
        """그 이름의 세션 NULL busy 잡(레거시, T11)."""
        busy = ",".join("?" * len(BUSY_STATES))
        return self._jobs(
            f"SELECT * FROM jobs WHERE worker_name=? AND worker_session_id IS NULL "
            f"AND state IN ({busy}) ORDER BY lane, id",
            (name, *sorted(BUSY_STATES)),
        )

    def closed_jobs_of(self, session_id: str, job_ids: Iterable[int]) -> list[dict[str, Any]]:
        """heartbeat 응답의 `closed_jobs`(결정 321 · 322) — 목록 가운데 그 세션의 이미 끝난 잡.
        `had_cancel` 은 잡 행에 취소 요청이 남아 있다는 뜻이다(F61)."""
        ids = sorted({int(j) for j in job_ids})
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        terminal = ",".join("?" * len(TERMINAL_STATES))
        return [
            {
                "id": int(r["id"]),
                "state": r["state"],
                "had_cancel": r["cancel_requested_at"] is not None,
            }
            for r in self._conn().execute(
                f"SELECT id, state, cancel_requested_at FROM jobs WHERE id IN ({marks}) "
                f"AND worker_session_id=? AND state IN ({terminal}) ORDER BY id",
                (*ids, session_id, *sorted(TERMINAL_STATES)),
            )
        ]

    def open_session(
        self,
        name: str,
        session_id: str,
        *,
        previous: Sequence[str],
        unverified: Sequence[str],
        now: datetime,
        pool: str,
        lanes: int,
        host_name: str | None,
        version: str | None,
    ) -> SessionOpened:
        """처음 보는 세션의 등록(§4.2 의 7) — 한 `BEGIN IMMEDIATE` 안에서 차례로 한다.

        1. `previous` 가운데 같은 이름인 세션의 busy 잡을 `lost`(`worker_restarted_without_job`).
           새 프로세스가 그 세션의 폴더 잠금을 쥐고 그룹이 사라졌다고 확인했다 — 홀드가 없다.
        2. 그 세션들의 이미 닫힌 잡에 열린 홀드가 있으면 푼다(`worker`, 토큰 이름).
        3. 그 이름의 세션 NULL(레거시) busy 잡을 오늘처럼 닫는다. 표식이면 홀드(T17) — 옛
           프로세스의 신원을 확인할 길이 없다.
        4. `unverified` 가운데 같은 이름인 세션에 `orphan_unverified = 1`.
        5. 그 세션들과 다른 현재 세션을 펜싱한다.
        6. 새 세션 행 · 워커 행.

        다른 이름의 세션 id 는 부르는 쪽이 먼저 403 으로 거른다(T19). 여기서는 같은 이름인 것만
        건드린다 — 남의 이름에 걸린 잡과 홀드를 풀 수 없게."""
        conn = self._conn()
        ts = _ts(now)
        busy = ",".join("?" * len(BUSY_STATES))
        terminal = ",".join("?" * len(TERMINAL_STATES))
        closed: list[int] = []
        released: list[int] = []
        fenced: list[str] = []
        text, code, args = outcome.summary("worker_restarted_without_job", name=name)
        conn.execute("BEGIN IMMEDIATE")
        try:
            mine = lambda ids: [  # noqa: E731
                r["id"]
                for r in conn.execute(
                    f"SELECT id FROM worker_sessions WHERE worker_name=? AND id IN "
                    f"({','.join('?' * len(ids))})",
                    (name, *ids),
                )
            ]
            prev = mine(sorted(set(previous))) if previous else []
            unver = mine(sorted(set(unverified))) if unverified else []
            for sid in prev:
                for row in conn.execute(
                    f"SELECT id FROM jobs WHERE worker_session_id=? AND state IN ({busy}) "
                    "ORDER BY id",
                    (sid, *sorted(BUSY_STATES)),
                ).fetchall():
                    self._close_lost_tx(conn, int(row["id"]), ts, text, code, args)
                    closed.append(int(row["id"]))
                for row in conn.execute(
                    f"SELECT id FROM jobs WHERE worker_session_id=? AND state IN ({terminal}) "
                    "AND id IN (SELECT job_id FROM irreversible_holds WHERE released_at IS NULL)",
                    (sid, *sorted(TERMINAL_STATES)),
                ).fetchall():
                    if self._release_hold_tx(
                        conn, int(row["id"]), kind=RELEASE_WORKER, by=name, reason=None, ts=ts
                    ):
                        released.append(int(row["id"]))
            legacy: list[int] = []
            for row in conn.execute(  # mutcheck: first-register-keeps-legacy
                f"SELECT id FROM jobs WHERE worker_name=? AND worker_session_id IS NULL "
                f"AND state IN ({busy}) ORDER BY id",
                (name, *sorted(BUSY_STATES)),
            ).fetchall():
                self._close_lost_tx(conn, int(row["id"]), ts, text, code, args)
                self._hold_tx(conn, int(row["id"]), "worker_restarted_without_job", ts)
                legacy.append(int(row["id"]))
            for sid in unver:
                conn.execute("UPDATE worker_sessions SET orphan_unverified=1 WHERE id=?", (sid,))
            fenced = [
                r["id"]
                for r in conn.execute(
                    "SELECT id FROM worker_sessions WHERE worker_name=? AND fenced_at IS NULL "
                    "AND id != ?",
                    (name, session_id),
                )
            ]
            fenced = sorted(set(fenced) | set(prev) | set(unver))
            for sid in fenced:
                conn.execute(
                    "UPDATE worker_sessions SET fenced_at=COALESCE(fenced_at, ?), "
                    "fenced_by=COALESCE(fenced_by, ?) WHERE id=?",
                    (ts, session_id, sid),
                )
            conn.execute(
                "INSERT INTO worker_sessions (id, worker_name, registered_at) VALUES (?, ?, ?)",
                (session_id, name, ts),
            )
            conn.execute(
                "INSERT INTO workers (name, pool, lanes, host_name, version, registered_at, "
                "last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(name) DO UPDATE SET "
                "pool=excluded.pool, lanes=excluded.lanes, host_name=excluded.host_name, "
                "version=excluded.version, last_seen_at=excluded.last_seen_at",
                (name, pool, int(lanes), host_name, version, ts, ts),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return SessionOpened(
            closed=closed + legacy, legacy=legacy, released=released, fenced=fenced
        )

    def _close_lost_tx(
        self,
        conn: sqlite3.Connection,
        job_id: int,
        ts: float,
        text: str,
        code: str | None,
        args: dict[str, Any] | None,
    ) -> None:
        self._set_state(
            conn,
            job_id,
            LOST,
            ts,
            finished_at=ts,
            summary=text[:200],
            summary_code=code,
            summary_args=dump_args(args),
            lane=None,
            phase=None,
        )

    # ── 그룹 홀드 (M5t-b 결정 330 · 339-d) ────────────────────────────────────

    def _is_irreversible_row(self, row: sqlite3.Row) -> bool:
        """그 잡이 표식 잡인가. claim 때 얼린 값이 있으면 그것이다. 레거시 잡(얼린 값 없음)은 닫는
        때 그 프리셋이 표식이거나 설정에 없으면 참이다 — 모름은 닫힌 쪽이다(결정 339-d)."""
        if row["irreversible"] is not None:
            return bool(row["irreversible"])
        if row["preset"] in self.irreversible:
            return True
        return self.known_presets is not None and row["preset"] not in self.known_presets

    def _hold_tx(self, conn: sqlite3.Connection, job_id: int, cause: str, ts: float) -> bool:
        """표식 잡이면 그 그룹의 홀드 행을 넣는다(호출자가 닫는 트랜잭션을 연다). 넣었으면 참.

        그룹 · 범위 · 풀 · 호스트는 잡 행을 복사한다. 표식 잡의 그룹 NULL 은 claim 이 이미 유효
        그룹으로 채웠다. 남는 NULL 은 레거시 잡이고 `irreversible:<프리셋>` · `global` 이다 — 아무
        잡도 막지 않는 `unknown:<job>` 같은 그룹은 만들지 않는다(열리는 쪽 금지)."""
        row = conn.execute(
            "SELECT preset, irreversible, concurrency_group, concurrency_scope, pool, host_key, "
            "worker_name FROM jobs WHERE id=?",
            (job_id,),
        ).fetchone()
        if row is None or not self._is_irreversible_row(row):  # mutcheck: hold-not-created
            return False
        group, scope = row["concurrency_group"], row["concurrency_scope"] or SCOPE_POOL
        if group is None:
            group, scope = SYNTHETIC_GROUP_PREFIX + row["preset"], SCOPE_GLOBAL
        cur = conn.execute(
            "INSERT OR IGNORE INTO irreversible_holds (job_id, concurrency_group, "
            "concurrency_scope, pool, host_key, worker_name, cause, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                job_id,
                group,
                scope,
                row["pool"] or DEFAULT_POOL,
                row["host_key"],
                row["worker_name"],
                cause,
                ts,
            ),
        )
        if cur.rowcount != 1:
            return False
        self._event(conn, job_id, EVENT_HOLD, {"action": "created", "cause": cause}, ts)
        return True

    def _release_hold_tx(
        self,
        conn: sqlite3.Connection,
        job_id: int,
        *,
        kind: str,
        by: str,
        reason: str | None,
        ts: float,
    ) -> bool:
        cur = conn.execute(
            "UPDATE irreversible_holds SET released_at=?, release_kind=?, released_by=?, "
            "release_reason=? WHERE job_id=? AND released_at IS NULL",
            (ts, kind, by, reason, job_id),
        )
        if cur.rowcount != 1:
            return False
        self._event(
            conn,
            job_id,
            EVENT_HOLD,
            {"action": "released", "release_kind": kind, "released_by": by, "reason": reason},
            ts,
        )
        return True

    def release_hold(
        self, job_id: int, *, kind: str, by: str, reason: str | None, now: datetime
    ) -> dict[str, Any] | None:
        """열린 홀드를 푼다 — 행 갱신과 사건이 한 트랜잭션이다. 열린 홀드가 없으면 None."""
        conn = self._conn()
        ts = _ts(now)
        conn.execute("BEGIN IMMEDIATE")
        try:
            done = self._release_hold_tx(conn, job_id, kind=kind, by=by, reason=reason, ts=ts)
            row = conn.execute(
                "SELECT * FROM irreversible_holds WHERE job_id=?", (job_id,)
            ).fetchone()
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        if not done or row is None:
            return None
        return {
            "job_id": job_id,
            "group": row["concurrency_group"],
            "released_at": _dt(row["released_at"]),
            "released_by": row["released_by"],
            "release_kind": row["release_kind"],
        }

    def has_open_hold(self, job_id: int) -> bool:
        return (
            self._conn()
            .execute(
                "SELECT 1 FROM irreversible_holds WHERE job_id=? AND released_at IS NULL",
                (job_id,),
            )
            .fetchone()
            is not None
        )

    # ── 원격 잡 로그 오프셋 (M5t-b §4.5 · 결정 323 · 338) ───────────────────

    def log_row(self, job_id: int) -> dict[str, Any] | None:
        """로그 쓰기 절차 step 4 — 잠금 안에서 다시 읽는 잡 행의 칸들."""
        row = (
            self._conn()
            .execute(
                "SELECT state, worker_name, worker_session_id, log_offset, log_file_bytes, "
                "log_complete FROM jobs WHERE id=?",
                (job_id,),
            )
            .fetchone()
        )
        return None if row is None else dict(row)

    def pin_log_incomplete(self, job_id: int) -> None:
        """`log.txt` 가 커밋된 길이보다 짧다(T16) — 그 잡의 로그는 끝까지 불완전하다."""
        self._conn().execute("UPDATE jobs SET log_complete=0 WHERE id=?", (job_id,))

    def commit_log(
        self,
        job_id: int,
        *,
        session: str,
        expect_offset: int,
        new_offset: int,
        file_bytes: int,
        markers: Sequence[tuple[str, str]],
        at: datetime,
    ) -> bool:
        """로그 쓰기 절차 step 8 — 오프셋 · 파일 길이 · 마커를 한 트랜잭션에. 잡이 닫혔거나 세션이
        펜싱됐거나(「세션이 아직 현재」, 결정 338) 오프셋이 움직였으면 0행이고 거짓이다."""
        busy = ",".join("?" * len(BUSY_STATES))
        ts = _ts(at)
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            cur = conn.execute(
                "UPDATE jobs SET log_offset=?, log_file_bytes=?, last_output_at=? "
                f"WHERE id=? AND COALESCE(log_offset, 0)=? AND state IN ({busy}) "
                "AND worker_session_id=? "
                # mutcheck: log-commit-ignores-fence
                "AND EXISTS (SELECT 1 FROM worker_sessions WHERE id=? AND fenced_at IS NULL)",
                (
                    new_offset,
                    file_bytes,
                    ts,
                    job_id,
                    expect_offset,
                    *sorted(BUSY_STATES),
                    session,
                    session,
                ),
            )
            if cur.rowcount != 1:
                conn.execute("ROLLBACK")
                return False
            conn.executemany(
                "INSERT INTO events (job_id, at, kind, payload) VALUES (?, ?, ?, ?)",
                [
                    (
                        job_id,
                        ts,
                        EVENT_MARKER,
                        json.dumps({"kind": k, "value": v}, separators=(",", ":")),
                    )
                    for k, v in markers
                ],
            )
            conn.execute("COMMIT")
            return True
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def remote_log_rows(self) -> list[dict[str, Any]]:
        """기동 정리 대상(§4.5) — 커밋 길이가 있는 busy 원격 잡과, 완전하다고 적힌 끝난 원격 잡."""
        busy = ",".join("?" * len(BUSY_STATES))
        rows = (
            self._conn()
            .execute(
                "SELECT id, state, log_file_bytes, log_complete FROM jobs "
                "WHERE worker_name IS NOT NULL AND log_file_bytes IS NOT NULL "
                f"AND (state IN ({busy}) OR log_complete=1) ORDER BY id",
                tuple(sorted(BUSY_STATES)),
            )
            .fetchall()
        )
        return [dict(r) for r in rows]

    def log_file_bytes_for(self, job_ids: Iterable[int]) -> dict[int, int]:
        """잡마다 커밋된 `log.txt` 길이(M5t-b 결정 339-f). NULL(로컬 · 레거시)인 잡은 빠진다."""
        ids = [int(j) for j in job_ids]
        out: dict[int, int] = {}
        for i in range(0, len(ids), 500):
            part = ids[i : i + 500]
            marks = ",".join("?" * len(part))
            for row in self._conn().execute(
                f"SELECT id, log_file_bytes FROM jobs WHERE id IN ({marks}) "
                "AND log_file_bytes IS NOT NULL",
                part,
            ):
                out[int(row["id"])] = int(row["log_file_bytes"])
        return out

    def log_complete_for(self, job_ids: Iterable[int]) -> dict[int, bool | None]:
        """잡마다 `log_complete`(참 · 거짓 · 모름 None). 없는 잡은 빠진다."""
        ids = [int(j) for j in job_ids]
        out: dict[int, bool | None] = {}
        for i in range(0, len(ids), 500):
            part = ids[i : i + 500]
            marks = ",".join("?" * len(part))
            for row in self._conn().execute(
                f"SELECT id, log_complete FROM jobs WHERE id IN ({marks})", part
            ):
                raw = row["log_complete"]
                out[int(row["id"])] = None if raw is None else bool(raw)
        return out

    def record_late_result(
        self,
        job_id: int,
        *,
        session: str,
        payload: dict[str, Any],
        log_bytes: int | None,
        gone: bool | None,
        by: str,
        now: datetime,
    ) -> tuple[bool, bool] | None:
        """늦은 결과(M5t-b §4.6 · 결정 256 · 274) — `BEGIN IMMEDIATE` 하나 안에서 한다.

        잡이 `lost` 이고 그 세션이 claim 했고 그 세션이 펜싱되지 않았을 때만 간다(아니면 None).
        `late_result` 사건이 아직 없을 때만 한 건 넣고 `log_complete` 를 finish 의 규칙으로 쓴다.
        잡 상태 · 요약 · 끝난 시각 · 로그 · 마커 · 산출물은 그대로다. `gone` 이 참이면 그 잡의 열린
        홀드를 푼다(결정 330). 돌려주는 것은 (기록했나, 홀드를 풀었나)."""
        ts = _ts(now)
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT state, worker_session_id, worker_name, log_offset, log_complete "
                "FROM jobs WHERE id=?",
                (job_id,),
            ).fetchone()
            srow = conn.execute(
                "SELECT fenced_at FROM worker_sessions WHERE id=?", (session,)
            ).fetchone()
            if (
                row is None
                or row["state"] != LOST
                or row["worker_session_id"] != session
                or srow is None
                or srow["fenced_at"] is not None  # mutcheck: fenced-late-result-recorded
            ):
                conn.execute("ROLLBACK")
                return None
            recorded = False
            seen = conn.execute(
                "SELECT 1 FROM events WHERE job_id=? AND kind=?", (job_id, EVENT_LATE_RESULT)
            ).fetchone()
            if seen is None:  # mutcheck: late-result-twice
                complete = _log_complete(row, log_bytes)
                # mutcheck: late-result-changes-state
                conn.execute("UPDATE jobs SET log_complete=? WHERE id=?", (complete, job_id))
                body = {**payload, "log_complete": None if complete is None else bool(complete)}
                self._event(conn, job_id, EVENT_LATE_RESULT, body, ts)
                recorded = True
            released = gone is True and self._release_hold_tx(
                conn, job_id, kind=RELEASE_WORKER, by=by, reason=None, ts=ts
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return recorded, bool(released)

    def late_result_of(self, job_id: int) -> dict[str, Any] | None:
        """그 잡의 `late_result` 사건(많아야 하나)의 내용. 없으면 None."""
        row = (
            self._conn()
            .execute(
                "SELECT payload FROM events WHERE job_id=? AND kind=? ORDER BY id LIMIT 1",
                (job_id, EVENT_LATE_RESULT),
            )
            .fetchone()
        )
        return None if row is None else json.loads(row["payload"])

    def open_holds(self) -> list[dict[str, Any]]:
        """열린 홀드(만든 순서). 칸은 `/api/status` 의 `irreversible_holds` 항목과 같다(§4.8)."""
        rows = self._conn().execute(
            "SELECT h.*, j.preset FROM irreversible_holds h LEFT JOIN jobs j ON j.id = h.job_id "
            "WHERE h.released_at IS NULL ORDER BY h.created_at, h.job_id"
        )
        return [
            {
                "job_id": int(r["job_id"]),
                "preset": r["preset"],
                "group": r["concurrency_group"],
                "scope": r["concurrency_scope"],
                "pool": r["pool"],
                "host_key": r["host_key"],
                "worker": r["worker_name"],
                "cause": r["cause"],
                "since": _dt(r["created_at"]),
            }
            for r in rows
        ]

    def hold_of(self, job_id: int) -> dict[str, Any] | None:
        """잡 문서의 `hold`(§4.8) — 열렸든 풀렸든 그 잡의 홀드와 사건 이력. 없으면 None."""
        conn = self._conn()
        row = conn.execute("SELECT * FROM irreversible_holds WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            return None
        history = [
            {"at": _dt(e["at"]), **json.loads(e["payload"] or "{}")}
            for e in conn.execute(
                "SELECT at, payload FROM events WHERE job_id=? AND kind=? ORDER BY id",
                (job_id, EVENT_HOLD),
            )
        ]
        return {
            "group": row["concurrency_group"],
            "scope": row["concurrency_scope"],
            "cause": row["cause"],
            "created_at": _dt(row["created_at"]),
            "released_at": _dt(row["released_at"]),
            "release_kind": row["release_kind"],
            "released_by": row["released_by"],
            "release_reason": row["release_reason"],
            "history": history,
        }

    # ── 서버 상태 ────────────────────────────────────────────────────────────

    def set_paused(self, by: str, now: datetime) -> None:
        self._conn().execute(
            "INSERT OR REPLACE INTO server_state (key, value) VALUES ('paused', ?)",
            (json.dumps({"by": by, "at": _ts(now)}),),
        )

    def clear_paused(self) -> None:
        self._conn().execute("DELETE FROM server_state WHERE key='paused'")

    def get_paused(self) -> Paused | None:
        row = self._conn().execute(_PAUSED_SQL).fetchone()
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
