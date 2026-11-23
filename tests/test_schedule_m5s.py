"""M5s A — 예약 실행의 순수 규칙과 저장소(`docs/m5s-workplan.md` §4.1 · §7 S1 S2 S6 S9 S10 S29).

- 시각 해석은 `core/schedule.py` 의 순수 함수다. 시간대와 `now` 를 받아서 벽시계를 보지 않는다.
- 저장소 시험은 `Store` 를 바로 부른다. 승격 · 만료 · claim 의 마감 필터 · 재시작 정리가
  모두 같은 트랜잭션 규칙(조건부 UPDATE)을 지나는지 본다.
- blob 보호(S9)는 `test_janitor_m5.py` 의 도우미로 manifest 와 blob 을 만든다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from remote_ci_monitor.core.model import (
    ACTIVE_STATES,
    CANCELLED,
    EXIT_CODE_BY_STATE,
    EXPIRED,
    QUEUED,
    SCHEDULED,
    SCHEDULED_STATES,
    TERMINAL_STATES,
    UPLOADING,
    Requester,
    Source,
)
from remote_ci_monitor.core.queue import QueueConfig, join_key, medians_from
from remote_ci_monitor.core.schedule import ScheduleError, parse_start_at, resolve_start
from remote_ci_monitor.store import DB_VERSION, Claimant, ScheduleExists, Store
from test_janitor import make_janitor
from test_janitor_m5 import blob_path, put_blob, write_manifest
from test_worker import make_config

UTC_T = datetime(2026, 11, 23, 1, 0, 0, tzinfo=UTC)  # 서울 10:00
SEOUL = ZoneInfo("Asia/Seoul")
NEW_YORK = ZoneInfo("America/New_York")
ALICE = Requester(name="alice-laptop", label="alice@laptop")


def at(seconds: float) -> datetime:
    return UTC_T + timedelta(seconds=seconds)


# ── 상태 집합 (결정 206) ────────────────────────────────────────────────────


def test_scheduled_is_outside_the_active_set_and_expired_is_terminal():
    assert SCHEDULED_STATES == frozenset({SCHEDULED})
    assert SCHEDULED not in ACTIVE_STATES
    assert SCHEDULED not in TERMINAL_STATES
    assert EXPIRED in TERMINAL_STATES
    assert EXIT_CODE_BY_STATE[EXPIRED] == 2  # 결정 212 — 돌지 않았다, cancelled 와 같은 줄


# ── 시각 해석 (결정 207 · S29) ──────────────────────────────────────────────


def test_an_offset_timestamp_is_taken_as_is():
    got = parse_start_at("2026-11-23T15:00:00+09:00", now=UTC_T, tz=NEW_YORK)
    assert got == datetime(2026, 11, 23, 6, 0, tzinfo=UTC)
    assert parse_start_at("2026-11-23T06:00Z", now=UTC_T, tz=SEOUL) == got


def test_a_date_and_time_without_offset_is_read_in_the_given_zone():
    got = parse_start_at("2026-11-23T15:00", now=UTC_T, tz=SEOUL)
    assert got == datetime(2026, 11, 23, 6, 0, tzinfo=UTC)
    assert got.tzinfo is not None and got.utcoffset() == timedelta(0)


def test_a_bare_hh_mm_is_today_in_the_given_zone():
    assert parse_start_at("15:00", now=UTC_T, tz=SEOUL) == datetime(2026, 11, 23, 6, 0, tzinfo=UTC)


def test_a_past_hh_mm_is_refused_and_never_moved_to_tomorrow():
    with pytest.raises(ScheduleError) as e:
        parse_start_at("09:00", now=UTC_T, tz=SEOUL)  # 서울은 지금 10:00
    assert e.value.code == "start_at_past"


@pytest.mark.parametrize("text", ["", "15", "25:00", "tomorrow", "2026-13-01T10:00", "15:00:00x"])
def test_garbage_is_start_at_invalid(text):
    with pytest.raises(ScheduleError) as e:
        parse_start_at(text, now=UTC_T, tz=SEOUL)
    assert e.value.code == "start_at_invalid"


def test_s29_a_time_skipped_by_daylight_saving_is_ambiguous():
    now = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    with pytest.raises(ScheduleError) as e:
        parse_start_at("2026-03-08T02:30", now=now, tz=NEW_YORK)  # 02:00 → 03:00
    assert e.value.code == "start_at_ambiguous"


def test_s29_a_time_that_happens_twice_is_ambiguous():
    now = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
    with pytest.raises(ScheduleError) as e:
        parse_start_at("2026-11-01T01:30", now=now, tz=NEW_YORK)  # 01:00–02:00 이 두 번
    assert e.value.code == "start_at_ambiguous"


def test_s29_an_offset_timestamp_on_the_boundary_is_fine():
    now = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
    got = parse_start_at("2026-11-01T01:30-04:00", now=now, tz=NEW_YORK)
    assert got == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)


def test_resolve_start_takes_seconds_from_now():
    assert resolve_start(None, 300, now=UTC_T, tz=SEOUL, max_ahead_days=14) == at(300)


def test_resolve_start_without_either_is_none():
    assert resolve_start(None, None, now=UTC_T, tz=SEOUL, max_ahead_days=14) is None


@pytest.mark.parametrize(
    ("start_at", "start_in", "code"),
    [
        ("15:00", 300, "start_at_conflict"),
        (None, 0, "start_in_invalid"),
        (None, -5, "start_in_invalid"),
        (None, True, "start_in_invalid"),
        (None, "300", "start_in_invalid"),
        (12, None, "start_at_invalid"),
        (None, 15 * 86400, "start_at_too_far"),
        ("2026-12-08T10:00+09:00", None, "start_at_too_far"),
    ],
)
def test_resolve_start_refuses(start_at, start_in, code):
    with pytest.raises(ScheduleError) as e:
        resolve_start(start_at, start_in, now=UTC_T, tz=SEOUL, max_ahead_days=14)
    assert e.value.code == code


def test_resolve_start_accepts_exactly_the_limit():
    got = resolve_start(None, 14 * 86400, now=UTC_T, tz=SEOUL, max_ahead_days=14)
    assert got == UTC_T + timedelta(days=14)


# ── 저장소 ──────────────────────────────────────────────────────────────────


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "rcm.sqlite3")
    yield s
    s.close()


def git_src(sha: str = "a" * 40) -> Source:
    return Source(mode="git_ref", repo="app", ref="main", sha=sha, base_sha=sha, dirty=False)


def tree_src(tree: str = "9f8e") -> Source:
    return Source(mode="tree", repo="org/app", tree_hash=tree, bytes=10)


def make(
    store: Store,
    *,
    source: Source | None = None,
    state: str = SCHEDULED,
    not_before: datetime | None = None,
    deadline: float | None = 900,
    inputs: dict | None = None,
    now: datetime = UTC_T,
    preset: str = "gate",
    priority: int = 0,
):
    source = source or git_src()
    inputs = {"scope": "full"} if inputs is None else inputs
    nb = not_before if not_before is not None else (at(600) if state != QUEUED else None)
    return store.create_job(
        preset=preset,
        inputs=inputs,
        key=f"{preset}:full",
        concurrency_group=None,
        source=source,
        requester=ALICE,
        timeout_seconds=1200,
        join_key=join_key(preset, inputs, source.identity),
        now=now,
        state=state,
        priority=priority,
        not_before=nb,
        start_deadline=(nb + timedelta(seconds=deadline)) if nb and deadline is not None else None,
    )


def test_db_is_v25_with_the_milestone_columns(store):
    assert DB_VERSION == 25
    cols = [r[1] for r in store._conn().execute("PRAGMA table_info(jobs)")]
    want = [
        "not_before",
        "start_deadline",
        "no_supersede",
        "chain_id",
        "retry_of",
        "attempt",
        "retry_on_exit",
        "retry_interval_seconds",
        "retry_max_attempts",
        "retry_disabled",
    ]
    assert cols[-len(want) :] == want


def test_a_v24_database_gains_the_columns_after_a_backup(tmp_path):
    import sqlite3

    path = tmp_path / "data" / "rcm.sqlite3"
    s = Store(path)
    old = make(s, state=QUEUED)
    s.close()
    c = sqlite3.connect(path)
    c.execute("DROP INDEX IF EXISTS jobs_chain")
    for col in (
        "retry_disabled",
        "retry_max_attempts",
        "retry_interval_seconds",
        "retry_on_exit",
        "attempt",
        "retry_of",
        "chain_id",
        "no_supersede",
        "start_deadline",
        "not_before",
    ):
        c.execute(f"ALTER TABLE jobs DROP COLUMN {col}")
    c.execute("PRAGMA user_version=24")
    c.commit()
    c.close()
    s = Store(path)
    try:
        assert s.user_version() == 25
        j = s.get_job(old.id)
        assert j.not_before is None and j.start_deadline is None and j.no_supersede is False
    finally:
        s.close()
    assert (path.parent / "backup" / "rcm.sqlite3.v24.bak").exists()


def test_a_scheduled_git_ref_job_waits_with_no_queued_at(store):
    j = make(store)
    assert j.state == SCHEDULED
    assert j.not_before == at(600) and j.start_deadline == at(1500)
    assert j.queued_at is None
    assert store.list_active() == []  # 활성 집합 밖(결정 206)
    assert [x.id for x in store.list_scheduled()] == [j.id]


def test_s1_promotion_waits_for_not_before_then_sets_queued_at(store):
    j = make(store)
    assert store.promote_due(at(599)) == []
    assert store.get_job(j.id).state == SCHEDULED
    assert store.promote_due(at(600)) == [j.id]
    got = store.get_job(j.id)
    assert got.state == QUEUED and got.queued_at == at(600)
    assert store.promote_due(at(601)) == []  # 조건부 UPDATE — 한 번만 오른다


def test_promotion_keeps_the_priority_set_at_submission(store):
    j = make(store, priority=1)
    store.promote_due(at(600))
    assert store.get_job(j.id).priority == 1


def test_expiry_closes_a_promoted_job_only_after_its_deadline(store):
    j = make(store)
    store.promote_due(at(600))
    assert store.expire_overdue(at(1500)) == []  # 마감 시각 그 자체는 아직 안이다
    assert store.get_job(j.id).state == QUEUED
    assert store.expire_overdue(at(1501)) == [j.id]
    got = store.get_job(j.id)
    assert got.state == EXPIRED and got.finished_at == at(1501)
    assert got.summary_code == "start_deadline_passed"


def test_expiry_never_touches_a_job_without_a_deadline(store):
    plain = make(store, state=QUEUED)
    assert store.expire_overdue(at(10**6)) == []
    assert store.get_job(plain.id).state == QUEUED


def test_expiry_closes_a_scheduled_job_whose_deadline_passed_before_promotion(store):
    j = make(store)
    assert store.expire_overdue(at(2000)) == [j.id]
    assert store.get_job(j.id).state == EXPIRED


@pytest.mark.parametrize("remote", [False, True])
def test_claim_skips_a_queued_job_past_its_deadline_on_both_sql_paths(store, remote):
    late = make(store)
    store.promote_due(at(600))
    fresh = make(store, inputs={"scope": "fast"}, not_before=at(1400))
    store.promote_due(at(1400))
    claimant = (
        Claimant(worker="build-02", pool="default", host_key=None, fingerprint_hash=None)
        if remote
        else None
    )
    got = store.claim(1, at(1600), claimant=claimant)
    assert got is not None and got.id == fresh.id
    assert store.get_job(late.id).state == QUEUED  # 집지 않았다 — 닫는 것은 주기 판정의 몫


def test_claim_reads_the_clock_again_inside_the_transaction(store):
    j = make(store)
    store.promote_due(at(600))
    # 호출자가 들고 온 시각(마감 안)이 아니라 트랜잭션 안에서 다시 읽은 시각(마감 뒤)을 쓴다
    assert store.claim(1, at(1000), now_fn=lambda: at(1600)) is None
    assert store.get_job(j.id).state == QUEUED


def test_s2_cancelling_a_scheduled_job_closes_it_at_once(store):
    j = make(store)
    assert store.request_cancel(j.id, "alice-laptop", at(10), 10) == CANCELLED
    assert store.get_job(j.id).state == CANCELLED
    assert store.promote_due(at(600)) == []


def test_bump_reaches_a_scheduled_job(store):
    j = make(store)
    assert store.set_priority(j.id, 1, at(1)) is True
    assert store.get_job(j.id).priority == 1


def test_s6_a_second_identical_schedule_is_refused_with_the_first_id(store):
    first = make(store)
    with pytest.raises(ScheduleExists) as e:
        make(store, not_before=at(900))
    assert e.value.job_id == first.id
    assert len(store.list_scheduled()) == 1


def test_s6_another_source_is_a_different_schedule(store):
    make(store)
    other = make(store, source=git_src("b" * 40))
    assert other.state == SCHEDULED


def test_a_tree_schedule_uploads_first_and_then_waits(store):
    j = make(store, source=tree_src(), state=UPLOADING)
    assert j.state == UPLOADING and j.not_before == at(600)
    assert store.mark_uploaded(j.id, 10, at(5)) is True
    got = store.get_job(j.id)
    assert got.state == SCHEDULED and got.queued_at is None


def test_a_tree_schedule_still_uploading_blocks_a_duplicate(store):
    first = make(store, source=tree_src(), state=UPLOADING)
    with pytest.raises(ScheduleExists) as e:
        make(store, source=tree_src(), state=UPLOADING, not_before=at(700))
    assert e.value.job_id == first.id


def test_recover_on_start_expires_late_promotes_due_and_keeps_future(store):
    future = make(store, inputs={"scope": "a"}, not_before=at(3600))
    due = make(store, inputs={"scope": "b"}, not_before=at(60))
    late = make(store, inputs={"scope": "c"}, not_before=at(60), deadline=100)
    lost, cancelled = store.recover_on_start(at(300))
    assert (lost, cancelled) == ([], [])  # 반환 모양은 그대로다
    assert store.schedule_recovered == ([late.id], [due.id])
    assert store.get_job(future.id).state == SCHEDULED
    assert store.get_job(due.id).state == QUEUED and store.get_job(due.id).queued_at == at(300)
    assert store.get_job(late.id).state == EXPIRED


def test_forgetting_workers_keeps_a_pool_that_has_a_schedule(store):
    store.register_worker("build-02", pool="linux", lanes=1, host_name=None, version="x", now=at(0))
    make(store, preset="lin")
    store._conn().execute("UPDATE jobs SET pool='linux'")
    assert store.forget_workers(at(100)) == []


# ── 대기 표본 (S10) ─────────────────────────────────────────────────────────


def test_s10_a_scheduled_jobs_wait_sample_starts_at_promotion(store):
    j = make(store)
    store.promote_due(at(600))
    got = store.claim(1, at(660))
    assert got is not None
    store.finish(j.id, "succeeded", now=at(760), exit_code=0)
    rows = store.list_sample_rows(at(-86400))
    med = medians_from(rows, at(800), QueueConfig(min_job_seconds=1))
    assert med["gate:full"].wait_seconds == 60  # 600 초(예약까지)가 아니라 승격부터 60 초


def test_s10_an_ordinary_jobs_wait_still_starts_at_creation(store):
    j = make(store, state=QUEUED)
    store.claim(1, at(90))
    store.finish(j.id, "succeeded", now=at(200), exit_code=0)
    med = medians_from(store.list_sample_rows(at(-86400)), at(300), QueueConfig(min_job_seconds=1))
    assert med["gate:full"].wait_seconds == 90


# ── blob 보호 (S9) ──────────────────────────────────────────────────────────


def test_s9_blobs_of_a_scheduled_tree_job_survive_the_sweep(tmp_path):
    cfg = make_config(tmp_path)
    cfg.server.snapshot_cache = True
    cfg.server.snapshot_cache_days = 30
    s = Store(cfg.data_dir / "rcm.sqlite3")
    try:
        j = make(s, source=tree_src(), state=UPLOADING)
        s.mark_uploaded(j.id, 10, at(1))
        assert s.get_job(j.id).state == SCHEDULED
        now = at(0) + timedelta(days=60)
        kept = put_blob(s, cfg, b"scheduled blob", used_at=now - timedelta(days=45))
        gone = put_blob(s, cfg, b"orphan blob", used_at=now - timedelta(days=45))
        write_manifest(cfg, j.id, [kept])
        jan, rec = make_janitor(s, cfg, now=now)
        jan.sweep_blobs(now)
        assert blob_path(cfg, kept).exists()
        assert not blob_path(cfg, gone).exists()
        assert rec.errors == []
    finally:
        s.close()
