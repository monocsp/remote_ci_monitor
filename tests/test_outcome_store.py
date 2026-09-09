"""저장소가 요약 코드를 남긴다(M5d-0, 오너 결정 37) — 스키마 v6 · 5→6 마이그레이션 ·
`finish(summary_code=, summary_args=)` 왕복 · 잡이 스스로 찍은 요약에는 코드가 없다 ·
서버가 직접 닫는 네 갈래(취소 · 재시작 · 업로드 중 재시작 · 방치된 업로드)의 문장은 그대로다.

문장이 그대로여야 하는 이유는 CLI 와 알림 훅이 `summary` 를 읽기 때문이다. 코드는 **곁들이는**
값이라, 코드가 붙었다고 읽는 사람이 보는 글자가 달라지면 그건 회귀다. 그래서 문장을 못 박고,
동시에 `render(code, args)` 가 같은 문장을 되돌려 주는지도 본다 — 화면이 코드로 다시 그릴 때
서버가 남긴 문장과 갈라지지 않게.

시각은 고정 NOW 기준으로 직접 찍는다(스레드 없음). 구현 전이라 빨간 것이 정상이다.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from remote_ci_monitor.core.model import (
    CANCELLED,
    FAILED,
    LOST,
    QUEUED,
    RUNNING,
    SUCCEEDED,
    UPLOADING,
    Job,
    Requester,
    Source,
)
from remote_ci_monitor.core.outcome import render
from remote_ci_monitor.core.queue import join_key
from remote_ci_monitor.store import DB_VERSION, Store

#: `recover_on_start` 가 찍는 문장이 `server restarted 2026-09-08 01:02:03Z` 가 되는 시각.
NOW = datetime(2026, 9, 8, 1, 2, 3, tzinfo=UTC)
RESTARTED_AT = "2026-09-08 01:02:03Z"
ALICE = Requester(name="alice-laptop", label="alice@laptop")


def at(seconds: float) -> datetime:
    return NOW + timedelta(seconds=seconds)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "rcm.sqlite3")
    yield s
    s.close()


def enqueue(
    store: Store,
    *,
    key: str = "gate:full",
    state: str = QUEUED,
    tree: str = "9f8e",
    now: datetime = NOW,
) -> Job:
    inputs = {"scope": "full"}
    src = Source(
        mode="tree", repo="org/app", base_sha="abc123f", dirty=True, tree_hash=tree, bytes=None
    )
    preset = key.split(":")[0]
    return store.create_job(
        preset=preset,
        inputs=inputs,
        key=key,
        concurrency_group=None,
        source=src,
        requester=ALICE,
        timeout_seconds=1200,
        join_key=join_key(preset, inputs, tree),
        now=now,
        state=state,
    )


def running(store: Store, *, tree: str = "9f8e", now: datetime = NOW) -> Job:
    """enqueue → claim. 그 레인에 다른 대기 잡이 없을 때 부른다(FIFO claim)."""
    j = enqueue(store, tree=tree, now=now)
    claimed = store.claim(1, now + timedelta(seconds=1))
    assert claimed is not None and claimed.id == j.id
    return claimed


def job_columns(path: Path) -> set[str]:
    with sqlite3.connect(path) as c:
        return {r[1] for r in c.execute("PRAGMA table_info(jobs)")}


def assert_code_redraws_the_sentence(job: Job) -> None:
    """서버가 남긴 문장 = 코드와 인자로 다시 그린 문장. 이게 어긋나면 화면이 다른 말을 하게 된다."""
    assert job.summary_code is not None, job.summary
    assert render(job.summary_code, job.summary_args) == job.summary


# ── 스키마 v6 ─────────────────────────────────────────────────────────────────


def test_fresh_db_is_schema_v6_with_the_two_summary_columns(store, tmp_path):
    assert store.user_version() == DB_VERSION and DB_VERSION >= 6
    assert {"summary_code", "summary_args"} <= job_columns(tmp_path / "rcm.sqlite3")


def test_migration_v5_to_v6_adds_the_columns_and_old_rows_have_no_code(tmp_path):
    path = tmp_path / "rcm.sqlite3"
    s = Store(path)
    done = enqueue(s, tree="d")
    assert s.claim(1, at(1)).id == done.id
    s.finish(done.id, FAILED, now=at(2), exit_code=1, summary="2 tests failed")
    live = enqueue(s, tree="q", now=at(3))  # 활성 행도 남긴다
    s.close()
    # v5 데이터베이스를 흉내 낸다: 새 열 둘을 떼고 user_version 을 5 로 되돌린다
    c = sqlite3.connect(path)
    try:
        c.execute("ALTER TABLE jobs DROP COLUMN concurrent_at_start")  # v10(M5f)
        c.execute("DROP INDEX IF EXISTS job_failures_name")
        c.execute("DROP INDEX IF EXISTS jobs_key_finished")
        c.execute("DROP TABLE IF EXISTS job_failures")
        c.execute("ALTER TABLE jobs DROP COLUMN fail_truncated")
        c.execute("ALTER TABLE jobs DROP COLUMN last_step")
        c.execute("ALTER TABLE jobs DROP COLUMN summary_code")
        c.execute("ALTER TABLE jobs DROP COLUMN summary_args")
        # v7(M5e)이 더한 것도 뗀다 — 안 그러면 6 뒤에 도는 7 이 중복 열로 죽는다
        c.execute("DROP INDEX IF EXISTS job_artifacts_expiry")
        c.execute("DROP TABLE IF EXISTS job_artifacts")
        c.execute("ALTER TABLE jobs DROP COLUMN join_count")
        c.execute("ALTER TABLE jobs DROP COLUMN failed_step_guessed")  # v11
        c.execute("PRAGMA user_version=5")
        c.commit()
        assert c.execute("PRAGMA user_version").fetchone()[0] == 5
    finally:
        c.close()
    assert not ({"summary_code", "summary_args"} & job_columns(path))
    s2 = Store(path)  # 5 → 6 마이그레이션이 여기서 돈다
    try:
        assert s2.user_version() == DB_VERSION and s2.healthy()
        assert {"summary_code", "summary_args"} <= job_columns(path)
        old = s2.get_job(done.id)
        assert old is not None and old.state == FAILED
        assert old.summary == "2 tests failed"  # 옛 문장은 그대로 읽힌다
        assert old.summary_code is None and old.summary_args == {}
        assert s2.get_job(live.id).state == QUEUED
        # 새 열이 실제로 쓸 수 있다 — 마이그레이션 뒤에 닫은 잡은 코드를 갖는다
        assert s2.finish(
            live.id,
            FAILED,
            now=at(4),
            exit_code=1,
            summary="exit 1",
            summary_code="exit_code",
            summary_args={"code": 1},
        )
        assert s2.get_job(live.id).summary_code == "exit_code"
        assert [j.summary_code for j in s2.list_recent(5)] == ["exit_code", None]
    finally:
        s2.close()
    s3 = Store(path)  # 두 번째 열기는 아무것도 바꾸지 않는다
    assert s3.user_version() == DB_VERSION and s3.get_job(done.id).summary_code is None
    s3.close()


# ── finish(summary_code=, summary_args=) 왕복 ────────────────────────────────


def test_finish_persists_the_code_and_the_raw_args(store):
    j = running(store)
    assert store.finish(
        j.id,
        FAILED,
        now=at(10),
        exit_code=1,
        summary="exit 1",
        summary_code="exit_code",
        summary_args={"code": 1},
    )
    got = store.get_job(j.id)
    assert got.state == FAILED and got.summary == "exit 1"
    assert got.summary_code == "exit_code"
    assert got.summary_args == {"code": 1}
    assert_code_redraws_the_sentence(got)


def test_recent_carries_the_code_and_args(store):
    a = running(store, tree="a")
    store.finish(
        a.id,
        LOST,
        now=at(10),
        summary="worker build-02 unreachable for 61s",
        summary_code="worker_unreachable",
        summary_args={"name": "build-02", "seconds": 61},
    )
    b = running(store, tree="b", now=at(20))
    store.finish(b.id, SUCCEEDED, now=at(30), exit_code=0)
    rows = {r.id: r for r in store.list_recent(10)}
    assert rows[a.id].summary_code == "worker_unreachable"
    assert rows[a.id].summary_args == {"name": "build-02", "seconds": 61}
    assert_code_redraws_the_sentence(rows[a.id])
    assert rows[b.id].summary is None and rows[b.id].summary_code is None


def test_raw_args_keep_their_types_not_pre_formatted_strings(store):
    """포맷은 보여 주는 쪽 몫이다 — 바이트 수는 숫자로, 이름은 이름으로 남는다(결정 37)."""
    j = running(store)
    store.finish(
        j.id,
        CANCELLED,
        now=at(10),
        summary="snapshot 700 MB exceeds 512 MB",
        summary_code="snapshot_too_big",
        summary_args={"bytes": 700_000_000, "limit": 512_000_000},
    )
    args = store.get_job(j.id).summary_args
    assert args == {"bytes": 700_000_000, "limit": 512_000_000}
    assert all(isinstance(v, int) for v in args.values())


def test_a_summary_the_job_printed_itself_has_no_code(store):
    """`::rcm::summary::` 로 팀이 찍은 문장은 번역 대상이 아니다 — 코드 없이 그대로 남는다."""
    j = running(store)
    assert store.finish(j.id, SUCCEEDED, now=at(10), exit_code=0, summary="3 flaky, 0 failed")
    got = store.get_job(j.id)
    assert got.summary == "3 flaky, 0 failed"
    assert got.summary_code is None and got.summary_args == {}
    assert render(got.summary_code, got.summary_args) is None  # 화면은 문장으로 물러선다


def test_the_columns_survive_a_reopen_of_the_same_file(tmp_path):
    path = tmp_path / "rcm.sqlite3"
    s = Store(path)
    j = running(s)
    s.finish(
        j.id,
        LOST,
        now=at(10),
        summary="worker build-02 restarted without the job",
        summary_code="worker_restarted_without_job",
        summary_args={"name": "build-02"},
    )
    plain = enqueue(s, tree="p", now=at(11))
    s.finish(plain.id, CANCELLED, now=at(12), summary="stopped by hand")
    s.close()
    s2 = Store(path)
    try:
        got = s2.get_job(j.id)
        assert got.summary == "worker build-02 restarted without the job"
        assert got.summary_code == "worker_restarted_without_job"
        assert got.summary_args == {"name": "build-02"}
        assert s2.get_job(plain.id).summary_code is None
    finally:
        s2.close()


# ── 서버가 직접 닫는 네 갈래: 문장은 그대로, 코드가 붙는다 ───────────────────


def test_cancel_before_start_keeps_its_sentence_and_gains_a_code(store):
    j = enqueue(store)
    assert store.request_cancel(j.id, "alice-laptop", at(5), 10.0) == CANCELLED
    got = store.get_job(j.id)
    assert got.state == CANCELLED and got.cancelled_by == "alice-laptop"
    assert got.summary == "cancelled before start"
    assert got.summary_code == "cancelled_before_start"
    assert_code_redraws_the_sentence(got)


def test_restart_recovery_keeps_its_sentence_and_gains_a_code(store):
    j = running(store)
    assert store.get_job(j.id).state == RUNNING
    lost, cancelled = store.recover_on_start(NOW)
    assert lost == [j.id] and cancelled == []
    got = store.get_job(j.id)
    assert got.state == LOST
    assert got.summary == f"server restarted {RESTARTED_AT}"
    assert got.summary_code == "server_restarted"
    assert_code_redraws_the_sentence(got)


def test_restart_during_upload_keeps_its_sentence_and_gains_a_code(store):
    j = enqueue(store, state=UPLOADING)
    lost, cancelled = store.recover_on_start(NOW)
    assert lost == [] and cancelled == [j.id]
    got = store.get_job(j.id)
    assert got.state == CANCELLED and got.cancelled_by == "server"
    assert got.summary == "server restarted during upload"
    assert got.summary_code == "server_restarted_during_upload"
    assert_code_redraws_the_sentence(got)


@pytest.mark.parametrize(("abandon_seconds", "sentence"), [(300, "5m"), (45, "45s")])
def test_abandoned_upload_keeps_its_sentence_and_gains_a_code(store, abandon_seconds, sentence):
    j = enqueue(store, state=UPLOADING)
    assert store.abandon_stale_uploads(at(abandon_seconds + 10), abandon_seconds) == [j.id]
    got = store.get_job(j.id)
    assert got.state == CANCELLED and got.cancelled_by == "server"
    assert got.summary == f"upload abandoned after {sentence}"
    assert got.summary_code == "upload_abandoned"
    assert_code_redraws_the_sentence(got)
    assert got.summary_args.get("seconds") == abandon_seconds  # 원시 값 — `5m` 이 아니다


# ── 인자가 깨져 있어도 조회는 산다 ───────────────────────────────────────────


def test_a_broken_args_column_reads_back_as_an_empty_dict(store, tmp_path):
    """DB 를 손으로 고쳐 넣은 쓰레기 때문에 상태 조회가 죽으면 안 된다."""
    j = running(store)
    store.finish(
        j.id,
        FAILED,
        now=at(10),
        summary="exit 1",
        summary_code="exit_code",
        summary_args={"code": 1},
    )
    with sqlite3.connect(tmp_path / "rcm.sqlite3") as c:
        c.execute("UPDATE jobs SET summary_args='{not json' WHERE id=?", (j.id,))
        c.commit()
    store.close()  # 다음 조회가 새 연결로 읽게 한다
    got: Any = store.get_job(j.id)
    assert got.summary == "exit 1" and got.summary_code == "exit_code"
    assert got.summary_args == {}
    assert render(got.summary_code, got.summary_args) == "exit ?"  # 값이 없으면 `?` 로 물러선다
