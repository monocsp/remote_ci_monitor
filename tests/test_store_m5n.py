"""M5n 저장소(역할 B) — DB v23 · `store.claim` 의 세 갈래와 범위 배제 · 머신 표 · 점검 기록 · 표본.

명세는 docs/m5n-workplan.md §5.2(claim 규칙 · `Claimant`) · §6(`concurrency_scope`) ·
§7.1(표본의 `host_key` · `sample`) · §11.1(DB v23) · §11.4(`store.py` 인터페이스). 시나리오 표는
docs/m5n-test-scenarios-b.md. 구현 전이라 빨간 것이 정상이다.

- 아직 없는 이름(`Claimant` · `upsert_machine` · …)은 모듈 최상단에서 import 하지 않는다 —
  파일이 통째로 수집 오류가 되면 어느 시나리오가 빨간지 안 보인다.
- 새 열(`placement` · `target_worker` · …)은 `create_job` 이 아니라 **SQL 로** 박는다
  (`set_cols`). claim 시나리오가 `create_job` 의 새 키워드 이름에 묶이지 않게 하려는 것이다.
  키워드 자체는 `test_create_job_takes_the_new_columns_as_keywords` 하나가 잠근다(가정 B-1).
- 시각은 고정 `NOW` 뿐이다 — sleep 도 스레드도 없다.
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from itertools import count
from pathlib import Path
from typing import Any

import pytest

from remote_ci_monitor.core.model import QUEUED, RUNNING, Requester, Source
from remote_ci_monitor.store import DB_VERSION, LaneBusy, Store

NOW = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
LOCAL = "@local"  # core/capability.LOCAL_WORKER — 문자열은 명세 §3 이 고정했다
HOST_A = "a1f3c9d2e4b5a6f7"  # Mac mini — 로컬 레인과 mac2-worker 가 같이 산다(운영 모양)
HOST_B = "7c20aa11bb22cc33"  # mbp — 다른 컴퓨터
FP_A = "f00dfeedcafe0001"
FP_B = "f00dfeedcafe0002"
NEW_JOB_COLUMNS = [
    "placement",
    "display_pool",
    "target_worker",
    "concurrency_scope",
    "host_key",
    "fingerprint_hash",
    "sample",
]
MACHINE_COLUMNS = [
    "worker",
    "host_key",
    "display_name",
    "accept_any",
    "fingerprint_hash",
    "fingerprint_json",
    "previous_json",
    "changed_at",
    "measured_at",
    "generation",
    "report_json",
]
_trees = count(1)


# ── 도우미 ───────────────────────────────────────────────────────────────────


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "rcm.sqlite3")
    yield s
    s.close()


def at(seconds: float) -> datetime:
    return NOW + timedelta(seconds=seconds)


def set_cols(store: Store, job_id: int, **cols: Any) -> None:
    """새 열을 SQL 로 직접 박는다(구현 전이면 `no such column` 으로 빨갛다)."""
    if not cols:
        return
    sets = ", ".join(f"{k}=?" for k in cols)
    store._conn().execute(f"UPDATE jobs SET {sets} WHERE id=?", (*cols.values(), job_id))


def add(
    store: Store,
    preset: str,
    *,
    pool: str = "default",
    placement: str = "pool",
    target: str | None = None,
    group: str | None = None,
    scope: str = "pool",
    priority: int = 0,
    now: datetime = NOW,
    **extra: Any,
) -> int:
    """queued 잡 하나. 합류 키는 없다 — 같은 프리셋을 여러 번 넣어도 새 잡."""
    job = store.create_job(
        preset=preset,
        inputs={},
        key=preset,
        concurrency_group=group,
        source=Source(mode="tree", tree_hash=f"tree-{next(_trees)}"),
        requester=Requester(name="alice-laptop", label="alice@host"),
        timeout_seconds=600,
        join_key=None,
        now=now,
        state=QUEUED,
        priority=priority,
        pool=pool,
    )
    set_cols(
        store,
        job.id,
        placement=placement,
        target_worker=target,
        concurrency_scope=scope,
        **extra,
    )
    return job.id


def claimant(worker: str, pool: str, host_key: str | None, fp: str | None, **kw: Any):
    from remote_ci_monitor.store import Claimant

    return Claimant(worker=worker, pool=pool, host_key=host_key, fingerprint_hash=fp, **kw)


def local(**kw: Any):
    return claimant(LOCAL, "default", HOST_A, FP_A, **kw)


def mac2(**kw: Any):
    return claimant("mac2-worker", "mac2", HOST_A, FP_A, **kw)


def mbp(**kw: Any):
    return claimant("mbp", "mbp", HOST_B, FP_B, **kw)


def take(store: Store, c, lane: int = 1, now: datetime = NOW):
    """claim 경로가 하는 그대로 — 로컬은 `worker_name=None`, 원격은 워커 이름을 같이 넘긴다."""
    worker_name = None if c.worker == LOCAL else c.worker
    return store.claim(lane, now, pool=c.pool, worker_name=worker_name, claimant=c)


def raw(store: Store, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    return store._conn().execute(sql, params).fetchall()


def columns(path: Path, table: str) -> list[str]:
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return [r[1] for r in c.execute(f"PRAGMA table_info({table})")]
    finally:
        c.close()


def index_columns(path: Path, index: str) -> list[str]:
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return [r[2] for r in c.execute(f"PRAGMA index_info({index})")]
    finally:
        c.close()


def file_version(path: Path) -> int:
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return int(c.execute("PRAGMA user_version").fetchone()[0])
    finally:
        c.close()


def downgrade_to_v22(path: Path, *, keep_columns: tuple[str, ...] = (), keep_machines=False):
    """이 빌드가 만든 DB 를 v22 모양으로 깎는다(있는 것만 지운다 — 구현 전에도 돈다)."""
    c = sqlite3.connect(path)
    try:
        c.execute("DROP INDEX IF EXISTS jobs_check")
        present = {r[1] for r in c.execute("PRAGMA table_info(jobs)")}
        for col in reversed(NEW_JOB_COLUMNS):
            if col in present and col not in keep_columns:
                c.execute(f"ALTER TABLE jobs DROP COLUMN {col}")
        tables = ["fingerprints", "machine_checks"]
        if not keep_machines:
            tables.append("machines")
        for table in tables:
            c.execute(f"DROP TABLE IF EXISTS {table}")
        c.execute("PRAGMA user_version=22")
        c.commit()
    finally:
        c.close()


# ── 1. DB v23 ────────────────────────────────────────────────────────────────


def test_this_build_is_database_version_24():
    # v24(M5p)가 `jobs.keep` 을 맨 끝에 붙이고 보관함 표를 더한다(tests/test_store_m5p.py)
    assert DB_VERSION == 26  # v25 는 M5s(tests/test_schedule_m5s.py) · v26 은 M5t-b


def test_a_fresh_database_has_the_v23_columns_tables_and_index(tmp_path):
    path = tmp_path / "data" / "rcm.sqlite3"
    s = Store(path)
    s.close()
    # 마이그레이션이 뒤에 붙이므로 새 DB 에서도 맨 끝이다 — 두 DB 의 열 차례가 같아야 한다
    # v24 의 `keep` 은 그 뒤 맨 끝이다(A18 · A19 가 잠근다)
    # 뒤로 v24 의 keep 하나와 v25(M5s) 열 열 개, v26(M5t-b) 열 일곱 개가 붙는다
    assert columns(path, "jobs")[-25:-18] == NEW_JOB_COLUMNS
    assert columns(path, "machines") == MACHINE_COLUMNS
    assert columns(path, "fingerprints") == ["hash", "json", "first_seen_at"]
    assert columns(path, "machine_checks") == [
        "worker",
        "preset",
        "fingerprint_hash",
        "job_id",
        "submitted_at",
        "auto",
    ]
    assert index_columns(path, "jobs_check") == ["worker_name", "preset", "fingerprint_hash", "id"]


def test_a_v22_database_migrates_after_a_v22_backup_and_old_rows_get_defaults(tmp_path):
    path = tmp_path / "data" / "rcm.sqlite3"
    s = Store(path)
    old = add(s, "gate") if "placement" in columns(path, "jobs") else None
    if old is None:  # 구현 전: 새 열이 없으니 옛 방식으로 만든다
        old = s.create_job(
            preset="gate",
            inputs={},
            key="gate",
            concurrency_group=None,
            source=Source(mode="tree", tree_hash="legacy"),
            requester=Requester(name="alice-laptop", label="alice@host"),
            timeout_seconds=600,
            join_key=None,
            now=NOW,
            state=QUEUED,
        ).id
    s.close()
    downgrade_to_v22(path)
    assert "placement" not in columns(path, "jobs")

    s = Store(path)
    try:
        assert s.user_version() == DB_VERSION
        # 깎는 도우미는 v24 의 `keep` 을 남긴다 — 그래서 다시 붙은 일곱 열이 맨 끝이다
        assert [c for c in columns(path, "jobs") if c != "keep"][-7:] == NEW_JOB_COLUMNS
        assert columns(path, "machines") == MACHINE_COLUMNS
        assert index_columns(path, "jobs_check") == [
            "worker_name",
            "preset",
            "fingerprint_hash",
            "id",
        ]
        job = s.get_job(old)
        # 옛 잡은 오늘의 뜻 그대로다 — 풀 배치 · 풀 범위 · 표본에 든다 · 호스트 모름(NULL)
        assert job.placement == "pool"
        assert job.display_pool is None
        assert job.target_worker is None
        assert job.concurrency_scope == "pool"
        assert job.host_key is None
        assert job.fingerprint_hash is None
        assert job.sample is True
    finally:
        s.close()
    bak = path.parent / "backup" / "rcm.sqlite3.v22.bak"
    assert bak.exists()
    assert file_version(bak) == 22
    assert "placement" not in columns(bak, "jobs")


def test_the_migration_survives_columns_and_tables_that_are_already_there(tmp_path):
    """번호가 밀려 옛 빌드가 열 일부를 먼저 더한 DB(M5h 의 교훈) — `duplicate column` 으로 죽지
    않고 끝까지 올라간다."""
    path = tmp_path / "rcm.sqlite3"
    Store(path).close()
    downgrade_to_v22(path)
    c = sqlite3.connect(path)
    c.execute("ALTER TABLE jobs ADD COLUMN placement TEXT NOT NULL DEFAULT 'pool'")
    c.execute("ALTER TABLE jobs ADD COLUMN host_key TEXT")
    c.execute(
        "CREATE TABLE machines (worker TEXT PRIMARY KEY, host_key TEXT, display_name TEXT, "
        "accept_any INTEGER NOT NULL DEFAULT 1, fingerprint_hash TEXT, fingerprint_json TEXT, "
        "previous_json TEXT, changed_at REAL, measured_at REAL, generation TEXT, "
        "report_json TEXT)"
    )
    c.execute("CREATE INDEX jobs_check ON jobs(worker_name, preset, id)")  # 모양이 달라도 견딘다
    c.commit()
    c.close()
    s = Store(path)
    try:
        assert s.user_version() == DB_VERSION
        assert set(NEW_JOB_COLUMNS) <= set(columns(path, "jobs"))
        assert columns(path, "fingerprints") == ["hash", "json", "first_seen_at"]
    finally:
        s.close()


def test_create_job_takes_the_new_columns_as_keywords(store):
    """가정 B-1: `create_job` 의 새 키워드 이름은 `Job` 필드 이름과 같다."""
    job = store.create_job(
        preset="sim-check",
        inputs={},
        key="sim-check",
        concurrency_group="sim",
        source=Source(mode="tree", tree_hash="kw"),
        requester=Requester(name="rcm:check", label="rcm (machine check)"),
        timeout_seconds=600,
        join_key=None,
        now=NOW,
        state=QUEUED,
        pool="mbp",
        placement="pool",
        target_worker="mbp",
        concurrency_scope="host",
        fingerprint_hash=FP_B,
        sample=False,
    )
    got = store.get_job(job.id)
    assert (got.target_worker, got.concurrency_scope, got.sample) == ("mbp", "host", False)
    assert got.fingerprint_hash == FP_B
    assert raw(store, "SELECT sample FROM jobs WHERE id=?", (job.id,))[0]["sample"] == 0


# ── 2. claim 의 세 갈래 (§5.2) ───────────────────────────────────────────────


def test_claimant_has_the_spec_fields_and_defaults():
    from remote_ci_monitor.store import Claimant

    assert [f.name for f in dataclasses.fields(Claimant)] == [
        "worker",
        "pool",
        "host_key",
        "fingerprint_hash",
        "capable",
        "gated",
        "accept_any",
        "known",  # 엣지 검증: 설정에 없는 프리셋의 any-capable 잡을 집어 바로 끝낸다
        "excluded",  # M5t-a 결정 254: 이 워커가 어느 갈래로도 못 집는 local_only 프리셋
    ]
    c = Claimant(worker="mbp", pool="mbp", host_key=HOST_B, fingerprint_hash=FP_B)
    assert (c.capable, c.gated, c.accept_any, c.known, c.excluded) == ((), (), True, (), ())
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.pool = "x"  # type: ignore[misc]


def test_a_targeted_job_goes_only_to_its_worker(store):
    jid = add(store, "sim-check", pool="mbp", target="mbp")
    # 다른 워커는 풀 · 능력과 무관하게 못 집는다
    assert take(store, local(capable=("sim-check",))) is None
    assert take(store, mac2(capable=("sim-check",))) is None
    job = take(store, mbp())
    assert job is not None and job.id == jid
    assert job.state == RUNNING
    assert (job.worker_name, job.host_key, job.fingerprint_hash) == ("mbp", HOST_B, FP_B)
    assert job.target_worker == "mbp"


def test_a_targeted_job_ignores_gated_capable_and_accept_any(store):
    """점검이 자기를 막으면 영원히 못 돈다(§4.5) · edge 14 — accept_any=false 여도 지정 잡은
    집는다."""
    jid = add(store, "sim-check", pool="mbp", target="mbp")
    job = take(store, mbp(gated=("sim-check",), capable=(), accept_any=False))
    assert job is not None and job.id == jid


def test_a_job_targeted_at_local_is_taken_by_a_local_lane_and_leaves_worker_name_null(store):
    jid = add(store, "sim-check", target=LOCAL)
    assert take(store, mac2()) is None
    job = take(store, local())
    assert job is not None and job.id == jid
    assert job.worker_name is None  # 로컬 claimant 의 worker_name 은 DB 에 NULL (§11.4)
    assert (job.host_key, job.fingerprint_hash) == (HOST_A, FP_A)


def test_a_pool_job_whose_preset_is_gated_waits_and_the_next_one_is_taken(store):
    gated = add(store, "scenario-qa")
    free = add(store, "gate")
    job = take(store, local(gated=("scenario-qa",)))
    assert job is not None and job.id == free
    assert take(store, local(gated=("scenario-qa",)), lane=2) is None
    assert store.get_job(gated).state == QUEUED
    job = take(store, local(), lane=2)  # 점검이 풀리면 집는다
    assert job is not None and job.id == gated


def test_a_pool_job_stays_in_its_pool(store):
    add(store, "remote-demo", pool="mac2")
    assert take(store, local(capable=("remote-demo",))) is None
    job = take(store, mac2())
    assert job is not None and job.pool == "mac2"


def test_a_pool_job_claimed_with_a_claimant_records_host_and_fingerprint(store):
    jid = add(store, "gate", pool="mac2")
    job = take(store, mac2())
    assert job.id == jid
    assert (job.pool, job.worker_name, job.host_key, job.fingerprint_hash) == (
        "mac2",
        "mac2-worker",
        HOST_A,
        FP_A,
    )
    assert job.placement == "pool"


def test_a_capable_job_is_taken_only_by_a_worker_that_lists_its_preset(store):
    jid = add(store, "gate", placement="capable")
    assert take(store, local()) is None
    assert take(store, mbp(capable=("other",))) is None
    job = take(store, mbp(capable=("gate",)))
    assert job is not None and job.id == jid


def test_a_capable_claim_rewrites_pool_and_keeps_the_waiting_pool_for_display(store):
    """edge 8 — 끝난 잡은 「실제로 돈 곳」으로 기록된다."""
    jid = add(store, "gate", placement="capable", pool="default")
    job = take(store, mbp(capable=("gate",)))
    assert job.id == jid
    assert job.pool == "mbp"
    assert job.display_pool == "default"
    assert (job.worker_name, job.host_key, job.fingerprint_hash) == ("mbp", HOST_B, FP_B)
    assert job.placement == "capable"
    row = raw(store, "SELECT pool, display_pool, host_key FROM jobs WHERE id=?", (jid,))[0]
    assert (row["pool"], row["display_pool"], row["host_key"]) == ("mbp", "default", HOST_B)


def test_a_capable_job_waiting_in_another_pool_moves_to_the_local_pool(store):
    jid = add(store, "gate", placement="capable", pool="mac2")
    job = take(store, local(capable=("gate",)))
    assert job.id == jid
    assert (job.pool, job.display_pool, job.worker_name) == ("default", "mac2", None)
    assert job.host_key == HOST_A


def test_a_worker_that_does_not_accept_any_never_takes_a_capable_job(store):
    """edge 14 — 장치 실험실처럼 전용으로 둔 머신."""
    add(store, "gate", placement="capable")
    assert take(store, mbp(capable=("gate",), accept_any=False)) is None


def test_priority_then_id_order_holds_across_the_three_kinds(store):
    p_low = add(store, "ok")  # pool · normal
    c_high = add(store, "gate", placement="capable", priority=1)
    t_norm = add(store, "sim-check", target=LOCAL)
    p_high = add(store, "ok", priority=1)
    c = local(capable=("gate",))
    order = [take(store, c, lane=n).id for n in (1, 2, 3, 4)]
    assert order == [c_high, p_high, p_low, t_norm]
    assert take(store, c, lane=5) is None


def test_a_remote_claimant_still_refuses_a_lane_that_has_a_job(store):
    add(store, "gate", placement="capable")
    add(store, "gate", placement="capable")
    assert take(store, mbp(capable=("gate",)), lane=1) is not None
    with pytest.raises(LaneBusy):
        take(store, mbp(capable=("gate",)), lane=1)


# ── 3. 그룹 범위 (§6 · edge 3) ───────────────────────────────────────────────


def test_host_scope_blocks_the_other_worker_on_the_same_host(store):
    """edge 3 — 운영 모양 그대로: 로컬 레인과 mac2-worker 는 같은 호스트다."""
    busy = add(store, "sim", pool="mac2", group="sim", scope="host")
    assert take(store, mac2()).id == busy
    waiting = add(store, "sim", pool="default", group="sim", scope="host")
    assert take(store, local()) is None
    assert store.get_job(waiting).state == QUEUED


def test_host_scope_lets_a_different_host_run_the_same_group(store):
    busy = add(store, "sim", pool="mac2", group="sim", scope="host")
    assert take(store, mac2()).id == busy
    other = add(store, "sim", placement="capable", group="sim", scope="host")
    job = take(store, mbp(capable=("sim",)))
    assert job is not None and job.id == other


def test_pool_scope_does_not_block_across_pools_even_on_the_same_host(store):
    """오늘의 동작 — `pool` 범위로는 같은 호스트의 두 워커를 못 막는다."""
    busy = add(store, "sim", pool="mac2", group="sim", scope="pool")
    assert take(store, mac2()).id == busy
    waiting = add(store, "sim", pool="default", group="sim", scope="pool")
    job = take(store, local())
    assert job is not None and job.id == waiting


def test_pool_scope_still_blocks_inside_the_same_pool(store):
    first = add(store, "qa", group="devices")
    second = add(store, "qa", group="devices")
    assert take(store, local()).id == first
    assert take(store, local(), lane=2) is None
    assert store.get_job(second).state == QUEUED


def test_global_scope_blocks_everywhere(store):
    busy = add(store, "store-upload", pool="mbp", group="store", scope="global")
    assert take(store, mbp()).id == busy
    add(store, "store-upload", pool="default", group="store", scope="global")
    add(store, "store-upload", placement="capable", group="store", scope="global")
    assert take(store, local(capable=("store-upload",))) is None
    assert take(store, mac2(capable=("store-upload",))) is None


def test_the_scope_of_the_waiting_job_decides_not_the_busy_one(store):
    """§6 — 배제는 **후보 잡의 범위**로 판정한다. 도는 잡이 `pool` 이어도 기다리는 잡이 `host` 면
    같은 호스트의 도는 잡에 막힌다."""
    busy = add(store, "sim", pool="mac2", group="sim", scope="pool")
    assert take(store, mac2()).id == busy
    add(store, "sim", pool="default", group="sim", scope="host")
    assert take(store, local()) is None


# ── 4. claimant=None 은 옛 경로 그대로 (회귀) ─────────────────────────────────


def test_claim_without_a_claimant_is_fifo_within_the_pool_and_records_no_host(store):
    a = add(store, "ok")
    b = add(store, "ok")
    first = store.claim(1, NOW)
    second = store.claim(2, NOW)
    assert (first.id, second.id) == (a, b)
    assert first.worker_name is None
    assert first.host_key is None and first.fingerprint_hash is None


def test_claim_without_a_claimant_keeps_the_in_pool_group_exclusion(store):
    a = add(store, "qa", group="devices")
    add(store, "qa", group="devices")
    c = add(store, "ok")
    assert store.claim(1, NOW).id == a
    assert store.claim(2, NOW).id == c
    assert store.claim(3, NOW) is None


# ── 5. 머신 표 (§11.1 · edge 9) ──────────────────────────────────────────────


def fp_json(value: str) -> str:
    return json.dumps({"xcode": {"state": "ok", "value": value}}, sort_keys=True)


def upsert(store: Store, worker: str, fp_hash: str | None, value: str, seconds: float, **kw):
    args: dict[str, Any] = {
        "host_key": HOST_B,
        "display_name": worker,
        "accept_any": True,
        "fingerprint_hash": fp_hash,
        "fingerprint_json": fp_json(value),
        "measured_at": at(seconds),
        "generation": "1731740000:0",
        "report_json": json.dumps({"presets": {}}),
    }
    args.update(kw)
    return store.upsert_machine(worker, **args)


def test_the_first_report_of_a_machine_is_not_a_change(store):
    row = upsert(store, "mbp", FP_A, "Xcode 26.5", 0)
    assert row.changed is False
    assert (row.worker, row.host_key, row.display_name) == ("mbp", HOST_B, "mbp")
    assert row.accept_any is True
    assert (row.fingerprint_hash, row.fingerprint_json) == (FP_A, fp_json("Xcode 26.5"))
    assert row.previous_json is None and row.changed_at is None
    assert row.measured_at == at(0)
    assert row.generation == "1731740000:0"
    assert [m.worker for m in store.list_machines()] == ["mbp"]


def test_the_same_fingerprint_again_is_not_a_change(store):
    upsert(store, "mbp", FP_A, "Xcode 26.5", 0)
    row = upsert(store, "mbp", FP_A, "Xcode 26.5", 600)
    assert row.changed is False
    assert row.previous_json is None and row.changed_at is None
    assert row.measured_at == at(600)


def test_a_different_fingerprint_keeps_the_previous_one_and_the_change_time(store):
    upsert(store, "mbp", FP_A, "Xcode 26.5", 0)
    row = upsert(store, "mbp", FP_B, "Xcode 27.2", 600)
    assert row.changed is True
    assert row.previous_json == fp_json("Xcode 26.5")
    assert row.changed_at == at(600)
    # 같은 지문이 다시 오면 「바뀐 날」은 그대로 남는다
    row = upsert(store, "mbp", FP_B, "Xcode 27.2", 1200)
    assert row.changed is False
    assert row.previous_json == fp_json("Xcode 26.5")
    assert row.changed_at == at(600)


def test_the_machine_table_remembers_the_fingerprint_across_a_restart(tmp_path):
    """edge 9 — 재시작 뒤 첫 보고에서 「바뀜」을 놓치지 않는다."""
    path = tmp_path / "rcm.sqlite3"
    s = Store(path)
    upsert(s, "@local", FP_A, "Xcode 26.5", 0, host_key=HOST_A, display_name="Mac mini")
    s.close()
    s = Store(path)
    try:
        rows = s.list_machines()
        assert [(m.worker, m.fingerprint_hash) for m in rows] == [("@local", FP_A)]
        row = upsert(s, "@local", FP_B, "Xcode 27.2", 60, host_key=HOST_A, display_name="Mac mini")
        assert row.changed is True
        assert row.previous_json == fp_json("Xcode 26.5")
        assert row.changed_at == at(60)
    finally:
        s.close()


def test_list_machines_is_ordered_by_worker_and_keeps_accept_any(store):
    upsert(store, "mbp", FP_A, "Xcode 26.5", 0, accept_any=False)
    upsert(store, "@local", FP_A, "Xcode 26.5", 0, host_key=HOST_A)
    upsert(store, "mac2-worker", FP_A, "Xcode 26.5", 0, host_key=HOST_A)
    rows = store.list_machines()
    assert [m.worker for m in rows] == ["@local", "mac2-worker", "mbp"]
    assert [m.accept_any for m in rows] == [True, True, False]


def test_record_fingerprint_keeps_the_first_sighting(store):
    store.record_fingerprint(FP_A, fp_json("Xcode 26.5"), at(0))
    store.record_fingerprint(FP_A, fp_json("Xcode 26.5"), at(600))
    rows = raw(store, "SELECT hash, json, first_seen_at FROM fingerprints")
    assert [(r["hash"], r["json"], r["first_seen_at"]) for r in rows] == [
        (FP_A, fp_json("Xcode 26.5"), at(0).timestamp())
    ]


# ── 6. 점검 기록 (§4.4) ──────────────────────────────────────────────────────


def as_tuples(records) -> list[tuple[str, int, str, str | None]]:
    return sorted((r.preset, r.job_id, r.job_state, r.fingerprint_hash) for r in records)


def test_check_records_are_the_check_jobs_of_that_worker(store):
    ran = add(store, "sim-check", pool="mbp", target="mbp")
    set_cols(store, ran, worker_name="mbp", fingerprint_hash=FP_A, state="failed")
    waiting = add(store, "sim-check", pool="mbp", target="mbp", fingerprint_hash=FP_B)
    by_pool = add(store, "sim-check", pool="mbp")  # 지정 없이 그 워커에서 돈 것도 기록이다
    set_cols(store, by_pool, worker_name="mbp", fingerprint_hash=FP_B, state="succeeded")
    other_worker = add(store, "sim-check", pool="mac2", target="mac2-worker")
    set_cols(store, other_worker, worker_name="mac2-worker", fingerprint_hash=FP_B)
    other_preset = add(store, "gate", pool="mbp")
    set_cols(store, other_preset, worker_name="mbp", fingerprint_hash=FP_B, state="failed")
    got = store.check_records("mbp", ["sim-check"])
    assert as_tuples(got) == [
        ("sim-check", ran, "failed", FP_A),
        ("sim-check", waiting, "queued", FP_B),
        ("sim-check", by_pool, "succeeded", FP_B),
    ]
    assert store.check_records("mbp", []) == []


def test_check_records_for_local_read_null_worker_name(store):
    ran = add(store, "sim-check", target=LOCAL)
    set_cols(store, ran, fingerprint_hash=FP_A, state="succeeded", started_at=NOW.timestamp())
    remote = add(store, "sim-check", pool="mbp", target="mbp")
    set_cols(store, remote, worker_name="mbp", fingerprint_hash=FP_A, state="succeeded")
    never_ran = add(store, "sim-check")  # 지정도 없고 아무도 안 집은 대기 잡 — 로컬 기록이 아니다
    got = store.check_records(LOCAL, ["sim-check"])
    assert as_tuples(got) == [("sim-check", ran, "succeeded", FP_A)]
    assert never_ran not in [r.job_id for r in got]


def test_auto_check_done_counts_only_automatic_submissions_per_fingerprint(store):
    assert store.auto_check_done("mbp", "sim-check", FP_A) is False
    store.record_machine_check("mbp", "sim-check", FP_A, 41, at(0), auto=True)
    assert store.auto_check_done("mbp", "sim-check", FP_A) is True
    assert store.auto_check_done("mbp", "sim-check", FP_B) is False  # 지문이 또 바뀌면 다시
    assert store.auto_check_done("mac2-worker", "sim-check", FP_A) is False
    store.record_machine_check("mbp", "sim-check", FP_B, 42, at(10), auto=False)  # 사람이 건 것
    assert store.auto_check_done("mbp", "sim-check", FP_B) is False
    rows = raw(
        store,
        "SELECT worker, preset, fingerprint_hash, job_id, submitted_at, auto "
        "FROM machine_checks ORDER BY submitted_at",
    )
    assert [tuple(r) for r in rows] == [
        ("mbp", "sim-check", FP_A, 41, at(0).timestamp(), 1),
        ("mbp", "sim-check", FP_B, 42, at(10).timestamp(), 0),
    ]


def test_a_check_can_be_recorded_twice_for_the_same_fingerprint_at_different_times(store):
    """PK 가 (worker, preset, fingerprint_hash, submitted_at) — 사람의 다시 점검은 몇 번이든."""
    store.record_machine_check("mbp", "sim-check", FP_A, 41, at(0), auto=True)
    store.record_machine_check("mbp", "sim-check", FP_A, 43, at(60), auto=False)
    n = raw(store, "SELECT count(*) AS n FROM machine_checks")[0]["n"]
    assert n == 2


# ── 7. 표본 — 호스트와 `sample` (§7.1) ───────────────────────────────────────


def finished(store: Store, key: str, seconds: float, **cols: Any) -> int:
    jid = add(store, key, now=at(-7200))
    set_cols(
        store,
        jid,
        state="succeeded",
        started_at=at(-3600).timestamp(),
        finished_at=at(-3600 + seconds).timestamp(),
        **cols,
    )
    return jid


def test_sample_rows_leave_out_jobs_marked_sample_false(store):
    finished(store, "gate", 300, host_key=HOST_A)
    finished(store, "sim-check", 120, host_key=HOST_A, sample=0)
    rows = store.list_sample_rows(at(-86400))
    assert [r.key for r in rows] == ["gate"]


def test_sample_rows_carry_the_raw_host_key_and_the_worker_name(store):
    """가정 B-8: 저장소는 날 값을 준다 — 옛 NULL 행을 로컬 · `worker:<이름>` 으로 푸는 것은
    서버(`_load_medians`)의 일이다(저장소는 로컬 호스트 키를 모른다)."""
    finished(store, "gate", 300, host_key=HOST_B, worker_name="mbp")
    finished(store, "gate", 310)  # 옛 로컬 행 — host_key NULL · worker_name NULL
    finished(store, "gate", 320, worker_name="mac2-worker")  # 옛 원격 행
    rows = store.list_sample_rows(at(-86400))
    assert [(r.host_key, r.worker_name) for r in rows] == [
        (HOST_B, "mbp"),
        (None, None),
        (None, "mac2-worker"),
    ]


def test_step_sample_ids_leave_out_jobs_marked_sample_false(store):
    a = finished(store, "gate", 300, host_key=HOST_A)
    finished(store, "gate", 300, host_key=HOST_A, sample=0)
    assert store.list_step_sample_ids("gate", since=at(-86400), limit=10) == [a]


def test_step_sample_ids_can_be_read_for_one_host(store):
    """가정 B-9: `list_step_sample_ids(..., host_key=H)` — 단계 중앙값은 다른 호스트로 안
    물러난다(§7.1). None(기본)이면 오늘처럼 모든 호스트."""
    on_a = finished(store, "gate", 300, host_key=HOST_A)
    on_b = finished(store, "gate", 300, host_key=HOST_B, worker_name="mbp")
    got = store.list_step_sample_ids("gate", since=at(-86400), limit=10, host_key=HOST_B)
    assert got == [on_b]
    got = store.list_step_sample_ids("gate", since=at(-86400), limit=10, host_key=HOST_A)
    assert got == [on_a]
    assert sorted(store.list_step_sample_ids("gate", since=at(-86400), limit=10)) == [on_a, on_b]
