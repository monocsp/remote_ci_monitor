"""저장소 — SQLite WAL. jobs · joiners · events · tokens · server_state.

- 연결은 스레드마다 하나(`threading.local`). autocommit 모드에서 필요한 곳만 `BEGIN IMMEDIATE`.
- `claim` 은 한 트랜잭션 안에서 「queued 이고 그룹이 running/cancelling 잡과 안 겹치는
  가장 작은 id」를 골라 `UPDATE … WHERE state='queued'` 로 잡는다(rowcount 로 원자성 확인).
- 상태 전이는 잡 갱신과 **같은 트랜잭션**에서 `events(kind='state')` 로 남긴다 → `transitions[]`.
- 시작 시 `running`·`cancelling` → `lost`, `uploading` → `cancelled`. 큐에서 사라지는 잡은 없다.
- 마이그레이션은 `PRAGMA user_version` 으로 번호를 매긴다.
- 시각은 DB 에 epoch 초(REAL)로 두고 모델에서는 UTC aware datetime.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from remote_ci_monitor.core.model import (
    ACTIVE_STATES,
    BUSY_STATES,
    CANCELLED,
    CANCELLING,
    LOST,
    PHASE_MATERIALIZING,
    QUEUED,
    RUNNING,
    TERMINAL_STATES,
    UPLOADING,
    CancelInfo,
    Job,
    Joiner,
    Paused,
    Requester,
    Source,
    Transition,
)
from remote_ci_monitor.core.progress import Marker

DB_VERSION = 2
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
  artifacts_purged_at REAL
);
CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state, id);
CREATE INDEX IF NOT EXISTS jobs_join ON jobs(join_key, state);
CREATE INDEX IF NOT EXISTS jobs_finished ON jobs(finished_at);
CREATE TABLE IF NOT EXISTS joiners (
  job_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  label TEXT NOT NULL,
  joined_at REAL NOT NULL,
  PRIMARY KEY (job_id, name)
);
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
  revoked_at REAL
);
CREATE TABLE IF NOT EXISTS server_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


#: v1 → v2: 보존 정리가 산출물을 지운 시각. 기존 DB 에 컬럼만 더한다.
_MIGRATIONS: dict[int, tuple[str, ...]] = {
    2: ("ALTER TABLE jobs ADD COLUMN artifacts_purged_at REAL",),
}


class StoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class TokenInfo:
    name: str
    admin: bool
    created_at: datetime
    revoked_at: datetime | None = None


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


def hash_token(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


class Store:
    """SQLite 저장소. 한 프로세스 안에서 여러 스레드가 같이 쓴다."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._local = threading.local()
        self._lock = threading.Lock()
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
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def migrate(self) -> None:
        """`PRAGMA user_version` 기준으로 빠진 마이그레이션만 적용한다."""
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
                        conn.execute(stmt)
                    conn.execute(f"PRAGMA user_version={target}")
                    conn.execute("COMMIT")
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
        elif version > DB_VERSION:
            raise StoreError(
                f"database schema version {version} is newer than this build ({DB_VERSION})"
            )

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
            base_sha=src.get("base_sha"),
            dirty=src.get("dirty"),
            tree_hash=src.get("tree_hash"),
            bytes=src.get("bytes"),
            received_bytes=row["received_bytes"],
            last_received_at=_dt(row["last_received_at"]),
            ref=src.get("ref"),
            sha=src.get("sha"),
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
            failed_step=row["failed_step"],
            lane=row["lane"],
            timeout_seconds=row["timeout_seconds"],
            cancel=cancel if row["state"] == CANCELLING else None,
            cancelled_by=row["cancelled_by"],
            phase=row["phase"],
            last_output_at=_dt(row["last_output_at"]),
            joiners=joiners,
            transitions=transitions,
            artifacts_purged_at=_dt(row["artifacts_purged_at"]),
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
        marks = ",".join("?" * len(TERMINAL_STATES))
        return self._jobs(
            f"SELECT * FROM jobs WHERE state IN ({marks}) "
            "ORDER BY finished_at DESC, id DESC LIMIT ?",
            (*sorted(TERMINAL_STATES), limit),
        )

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

    def delete_old_jobs(self, cutoff: datetime) -> int:
        """산출물이 이미 지워진 종료 잡 중 cutoff 전에 끝난 것의 행·이벤트·합류자를 지운다."""
        marks = ",".join("?" * len(TERMINAL_STATES))
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            ids = [
                int(r[0])
                for r in conn.execute(
                    f"SELECT id FROM jobs WHERE state IN ({marks}) "
                    "AND artifacts_purged_at IS NOT NULL "
                    "AND COALESCE(finished_at, created_at) < ?",
                    (*sorted(TERMINAL_STATES), _ts(cutoff)),
                ).fetchall()
            ]
            if ids:
                id_marks = ",".join("?" * len(ids))
                conn.execute(f"DELETE FROM events WHERE job_id IN ({id_marks})", ids)
                conn.execute(f"DELETE FROM joiners WHERE job_id IN ({id_marks})", ids)
                conn.execute(f"DELETE FROM jobs WHERE id IN ({id_marks})", ids)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return len(ids)

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
    ) -> Job:
        """새 잡. tree 는 `uploading`, git_ref 는 바로 `queued` 로 만든다."""
        if state not in (UPLOADING, QUEUED):
            raise StoreError(f"new job cannot start in state {state}")
        conn = self._conn()
        src = {
            "mode": source.mode,
            "repo": source.repo,
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
                "timeout_seconds, join_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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

    def claim(self, lane: int, now: datetime) -> Job | None:
        """queued 잡 하나를 원자적으로 running 으로. 그룹이 겹치면 건너뛴다."""
        conn = self._conn()
        ts = _ts(now)
        conn.execute("BEGIN IMMEDIATE")
        try:
            busy = ",".join("?" * len(BUSY_STATES))
            row = conn.execute(
                "SELECT id FROM jobs WHERE state=? AND (concurrency_group IS NULL OR "
                "concurrency_group NOT IN (SELECT concurrency_group FROM jobs "
                f"WHERE state IN ({busy}) "
                "AND concurrency_group IS NOT NULL)) ORDER BY id LIMIT 1",
                (QUEUED, *sorted(BUSY_STATES)),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            job_id = int(row["id"])
            cur = conn.execute(
                "UPDATE jobs SET state=?, lane=?, started_at=?, phase=?, last_output_at=? "
                "WHERE id=? AND state=?",
                (RUNNING, lane, ts, PHASE_MATERIALIZING, ts, job_id, QUEUED),
            )
            if cur.rowcount != 1:
                conn.execute("ROLLBACK")
                return None
            self._event(conn, job_id, EVENT_STATE, {"state": RUNNING}, ts)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return self.get_job(job_id)

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
                    summary="cancelled before start",
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
        failed_step: str | None = None,
        cancelled_by: str | None = None,
        only_from: Iterable[str] | None = None,
    ) -> bool:
        """종료 상태로. `only_from` 을 주면 그 상태에서만 바뀐다(경쟁 방지)."""
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
                failed_step=failed_step,
                cancelled_by=cancelled_by if cancelled_by is not None else row["cancel_by"],
                lane=None,
                phase=None,
            )
            conn.execute("COMMIT")
            return True
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def recover_on_start(self, now: datetime) -> tuple[list[int], list[int]]:
        """서버 시작 정리. running·cancelling → lost, uploading → cancelled."""
        conn = self._conn()
        ts = _ts(now)
        when = now.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%SZ")
        recover_state = LOST  # mutcheck: restart-lost — 조용히 succeeded/queued 로 되돌리지 않는다
        lost: list[int] = []
        cancelled: list[int] = []
        conn.execute("BEGIN IMMEDIATE")
        try:
            for row in conn.execute(
                "SELECT id FROM jobs WHERE state IN (?, ?) ORDER BY id", (RUNNING, CANCELLING)
            ).fetchall():
                self._set_state(
                    conn,
                    row["id"],
                    recover_state,
                    ts,
                    finished_at=ts,
                    summary=f"server restarted {when}",
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
                    summary="server restarted during upload",
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
        self._event(self._conn(), job_id, EVENT_MARKER, {"kind": kind, "value": value}, _ts(at))

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

    def add_token(self, name: str, *, admin: bool, now: datetime) -> str:
        """무작위 32바이트 토큰을 만들어 **한 번만** 돌려준다. DB 에는 sha256 만 남는다."""
        secret = secrets.token_urlsafe(32)
        try:
            self._conn().execute(
                "INSERT INTO tokens (name, sha256, admin, created_at) VALUES (?, ?, ?, ?)",
                (name, hash_token(secret), 1 if admin else 0, _ts(now)),
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
            "SELECT name, sha256, admin, created_at FROM tokens WHERE revoked_at IS NULL"
        ):
            if hmac.compare_digest(row["sha256"], digest):
                return TokenInfo(
                    name=row["name"], admin=bool(row["admin"]), created_at=_dt(row["created_at"])
                )
        return None

    def list_tokens(self) -> list[TokenInfo]:
        return [
            TokenInfo(
                name=r["name"],
                admin=bool(r["admin"]),
                created_at=_dt(r["created_at"]),
                revoked_at=_dt(r["revoked_at"]),
            )
            for r in self._conn().execute(
                "SELECT name, admin, created_at, revoked_at FROM tokens ORDER BY created_at, name"
            )
        ]

    def revoke_token(self, name: str, now: datetime) -> bool:
        cur = self._conn().execute(
            "UPDATE tokens SET revoked_at=? WHERE name=? AND revoked_at IS NULL", (_ts(now), name)
        )
        return cur.rowcount == 1

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
            mins = int(abandon_seconds // 60)
            if self.finish(
                int(row["id"]),
                CANCELLED,
                now=now,
                summary=f"upload abandoned after {mins}m",
                cancelled_by="server",
                only_from=(UPLOADING,),
            ):
                out.append(int(row["id"]))
        return out


__all__ = ["Store", "StoreError", "TokenInfo", "hash_token", "DB_VERSION"]
