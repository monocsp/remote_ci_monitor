"""저장소(M5h) — 종료 규칙 · DB v11(`last_step`) · v12(실패 대장) · 창 질의.

명세는 `docs/m5h-implementation.md` §1.2(`outcome_for`) · §1.3(v11) · §2.1(v12 · 대장 쓰기 ·
`failure_stats` · 삭제). 시나리오는 `docs/m5h-test-scenarios-b.md`. **구현 전이라 빨간 것이
정상이다.**

- 시각은 고정 `NOW` 기준으로 넘긴다 — sleep 도 벽시계도 스레드도 없다.
- 아직 없는 이름(`Outcome.last_step` · `finish(fail_names=…)` · `failure_stats`)은 **테스트
  안에서** 만난다. 모듈 최상단에서 import 하면 파일 하나가 통째로 수집 오류가 되고, 어느
  시나리오가 빨간지 알 수 없다(`tests/test_store_m5e.py` 와 같은 관례).
- `core/failures.FailureRow` 는 역할 A 의 파일이다 — 여기서는 import 하지 않고 `name` ·
  `seen` · `first_seen_job_id` · `last_seen_job_id` 네 칸만 읽는다.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from jobfactory import job as make_job
from remote_ci_monitor.core.model import (
    CANCELLED,
    FAILED,
    LOST,
    QUEUED,
    RUNNING,
    SUCCEEDED,
    TIMED_OUT,
    CancelInfo,
    Job,
    Requester,
    Source,
)
from remote_ci_monitor.core.progress import Marker
from remote_ci_monitor.core.queue import join_key
from remote_ci_monitor.store import _MIGRATIONS, _SCHEMA_V1, DB_VERSION, Store, StoreError
from remote_ci_monitor.worker import outcome_for

NOW = datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC)
ALICE = Requester(name="alice-laptop", label="alice@laptop")

#: 잡 하나가 남길 수 있는 실패 이름 수(§1.1 `MAX_FAIL_NAMES`). 계약이라 숫자로 박아 둔다 —
#: `core/progress.py` 는 역할 A 의 파일이라 여기서 상수를 빌려 오지 않는다.
MAX_FAIL_NAMES = 100

#: 운영 잡 #176 이 취소될 때 **마지막으로 되재생된 머리말**(워크플랜 §2.2).
#: 그 잡의 요약은 깨끗한 `cancelled by macbook` 이었다 — 신고자가 본 무관한 문자열은
#: 그 옆에 붙은 `failed_step` 이고, 이 상수가 이 마일스톤의 증인이다.
STEP_176 = "디바이스 QA chunk (실행기 스텝 · OS 권한 · 진행판 동기)"

#: #162 의 마지막 머리말(워크플랜 §2.1). 성공한 스텝이 실패 스텝으로 보고됐다.
STEP_162 = "build web (release — 셰이더 impellerc 컴파일 회귀 포함)"


def at(seconds: float) -> datetime:
    return NOW + timedelta(seconds=seconds)


STARTED = NOW
FINISHED = at(600)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "rcm.sqlite3")
    yield s
    s.close()


def raw(store: Store) -> sqlite3.Connection:
    """DB 를 직접 읽는다 — 대장에는 읽기 API 가 `failure_stats` 하나뿐이다(§2.1)."""
    conn = sqlite3.connect(store.path)
    conn.row_factory = sqlite3.Row
    return conn


def enqueue(
    store: Store,
    *,
    key: str = "gate",
    tree: str = "9f8e",
    now: datetime = NOW,
    state: str = QUEUED,
) -> Job:
    src = Source(mode="tree", repo="org/app", base_sha="abc123f", dirty=True, tree_hash=tree)
    return store.create_job(
        preset=key.split(":")[0],
        inputs={},
        key=key,
        concurrency_group=None,
        source=src,
        requester=ALICE,
        timeout_seconds=1200,
        join_key=join_key(key.split(":")[0], {}, tree),
        now=now,
        state=state,
    )


def finished(
    store: Store,
    *,
    seconds: float,
    state: str = FAILED,
    key: str = "gate",
    names: tuple[str, ...] = (),
    truncated: bool = False,
    failed_step: str | None = None,
    last_step: str | None = None,
) -> int:
    """`key` 의 종료 잡 하나. 창 시나리오는 `seconds`(= `finished_at`) 로만 순서를 만든다."""
    j = enqueue(store, key=key, tree=f"{key}-{seconds}", now=at(seconds - 1))
    ok = store.finish(
        j.id,
        state,
        now=at(seconds),
        exit_code=0 if state == SUCCEEDED else 1,
        failed_step=failed_step,
        last_step=last_step,
        fail_names=list(names),
        fail_truncated=truncated,
    )
    assert ok is True
    return j.id


def ledger(store: Store, job_id: int) -> list[str]:
    """대장에 남은 이름을 `seq` 순으로. 순서는 **잡이 찍은 순서**다(§2.2)."""
    with raw(store) as conn:
        rows = conn.execute(
            "SELECT name, seq FROM job_failures WHERE job_id=? ORDER BY seq", (int(job_id),)
        ).fetchall()
    seqs = [int(r["seq"]) for r in rows]
    assert seqs == sorted(seqs), seqs  # seq 는 1부터 단조 증가한다(§2.1)
    return [r["name"] for r in rows]


def stats(store: Store, job_id: int, *, key: str = "gate", window: int = 20):
    """`failure_stats(job_id, key, finished_at, *, window) -> (rows, window_jobs, unnamed)`.

    앵커(`finished_at`)는 서버가 하는 것과 똑같이 그 잡의 행에서 읽는다(§2.3).
    """
    job = store.get_job(job_id)
    assert job is not None and job.finished_at is not None
    rows, window_jobs, unnamed = store.failure_stats(job_id, key, job.finished_at, window=window)
    return list(rows), int(window_jobs), int(unnamed)


def seen_by_name(rows) -> dict[str, int]:
    return {r.name: r.seen for r in rows}


def markers(*specs: tuple[str, str], start: datetime = NOW) -> list[Marker]:
    """`("step", "build")` 꼴을 1초 간격 마커로. 시각은 고정이다 — 벽시계를 안 본다."""
    return [
        Marker(at=start + timedelta(seconds=i), kind=kind, value=value)
        for i, (kind, value) in enumerate(specs, start=1)
    ]


def running_job(**kw: Any) -> Job:
    """`outcome_for` 에 넣을 도는 잡 하나."""
    return make_job(700, key="gate", state=RUNNING, started_min=10, timeout_seconds=1200, **kw)


# ══ A. `outcome_for` — 어느 상태가 스텝 라벨을 갖나 (§1.2) ═══════════════════


def test_a_failed_job_carries_the_declared_step_and_its_names():
    """`failed` 는 세 칸을 전부 갖는다 — 선언이 있었으니 가질 자격이 있다."""
    ms = markers(("step", "test"), ("fail", "test"), ("fail", "port_test.dart"))
    oc = outcome_for(running_job(), ms, started=STARTED, finished=FINISHED, rc=1)
    assert oc.state == FAILED
    assert oc.failed_step == "test"
    assert oc.last_step == "test"
    assert oc.fail_names == ("test", "port_test.dart")
    assert oc.fail_truncated is False


def test_a_cancelled_job_keeps_no_step_label_bug_176():
    """운영 잡 #176 — **취소된 잡이 실패 스텝을 갖고 있었다**(워크플랜 §2.2 · 결정 64).

    요약은 처음부터 깨끗했다. 신고자가 본 무관한 문자열은 옆에 붙은 `failed_step` 이고,
    그 값은 취소 시점에 마지막으로 되재생된 머리말이었다. 이 테스트가 그 증인이다.
    """
    j = running_job(cancel=CancelInfo(requested_at=at(300), by="macbook", kill_at=at(310)))
    ms = markers(("step", "무거운 셋 병렬 시작"), ("step", STEP_176))
    oc = outcome_for(j, ms, started=STARTED, finished=FINISHED, rc=-15, cancelled=True)
    assert oc.state == CANCELLED
    assert oc.summary == "cancelled by macbook"  # 요약은 새지 않았다 — 라벨이 샜다
    assert oc.failed_step is None  # 오늘은 STEP_176 이 여기 붙는다
    assert oc.last_step is None  # 사람이 세운 잡에 스텝 라벨을 안 붙인다
    assert oc.fail_names == ()


def test_a_cancelled_job_that_declared_a_failure_names_nothing():
    """취소 직전에 이름을 찍었어도 대장에 안 들어간다 — 그 잡은 판정을 못 냈다."""
    j = running_job(cancel=CancelInfo(requested_at=at(300), by="macbook", kill_at=at(310)))
    ms = markers(("step", "test"), ("fail", "test"))
    oc = outcome_for(j, ms, started=STARTED, finished=FINISHED, rc=-15, cancelled=True)
    assert oc.state == CANCELLED
    assert (oc.failed_step, oc.last_step, oc.fail_names) == (None, None, ())


def test_a_lost_job_keeps_no_step_label_and_no_names():
    """서버가 잃은 잡도 마찬가지다 — 잡은 아무 판정도 못 냈다(결정 64)."""
    ms = markers(("step", "test"), ("fail", "test"))
    oc = outcome_for(running_job(), ms, started=STARTED, finished=FINISHED, rc=None, lost=True)
    assert oc.state == LOST
    assert (oc.failed_step, oc.last_step, oc.fail_names) == (None, None, ())


def test_a_timed_out_job_keeps_the_last_step_without_naming_a_failed_one():
    """강제 종료에 `exit_code=1` 을 넣던 폴백이 사라진다(§1.2 · 워크플랜 §13-2).

    열린 채 끝난 마지막 스텝은 「모른다」(`ok is None`)로 닫힌다. 그래서 `failed_step` 은
    비고 `last_step` 만 「끝났을 때 어디였나」를 말한다. #162 의 모양 그대로다.
    """
    ms = markers(("step", "test"), ("step", STEP_162))
    oc = outcome_for(running_job(), ms, started=STARTED, finished=FINISHED, rc=None, timed_out=True)
    assert oc.state == TIMED_OUT
    assert oc.failed_step is None  # 오늘은 STEP_162 가 여기 붙는다
    assert oc.last_step == STEP_162
    assert oc.fail_names == ()


def test_a_timed_out_job_keeps_the_names_it_declared():
    """`STEP_STATES = (FAILED, TIMED_OUT)` — 시한에 걸린 잡의 선언은 대장에 남는다."""
    ms = markers(("step", "test"), ("fail", "flaky_port_test.dart"))
    oc = outcome_for(running_job(), ms, started=STARTED, finished=FINISHED, rc=None, timed_out=True)
    assert oc.state == TIMED_OUT
    assert oc.fail_names == ("flaky_port_test.dart",)
    assert oc.last_step == "test"
    assert oc.failed_step is None  # 스텝 이름이 아닌 단위를 지목했다


def test_a_succeeded_job_that_declared_a_failure_records_nothing():
    """**잡 자신의 판정이 이긴다.** 정보성 스텝이 이름을 찍고 exit 0 이면 대장에 안 들어간다."""
    ms = markers(("step", "test"), ("fail", "test"), ("summary", "all green"))
    oc = outcome_for(running_job(), ms, started=STARTED, finished=FINISHED, rc=0)
    assert oc.state == SUCCEEDED
    assert oc.summary == "all green"
    assert (oc.failed_step, oc.last_step, oc.fail_names) == (None, None, ())


def test_the_three_tuple_unpacking_is_unchanged():
    """`Outcome.__iter__` 는 그대로 둔다(§1.2) — 옛 호출부가 3-튜플로 푼다."""
    ms = markers(("step", "test"), ("fail", "test"))
    state, summary, failed_step = outcome_for(
        running_job(), ms, started=STARTED, finished=FINISHED, rc=1
    )
    # summary 는 M5h 가 안 건드린다 — 마커 요약이 없는 실패 잡은 예나 지금이나 `exit N` 이다
    # (`core/outcome.py` 의 `exit_code`). 여기서 잠그는 것은 **3-튜플로 풀린다**는 것뿐이다.
    assert (state, summary, failed_step) == (FAILED, "exit 1", "test")


def test_a_failed_job_that_named_too_many_things_is_truncated():
    """잡당 100개 상한(R6). 넘친 것은 버리고 `fail_truncated` 로 밝힌다."""
    over = markers(("step", "test"), *[("fail", f"case{i}") for i in range(MAX_FAIL_NAMES + 1)])
    oc = outcome_for(running_job(), over, started=STARTED, finished=FINISHED, rc=1)
    assert len(oc.fail_names) == MAX_FAIL_NAMES
    assert oc.fail_truncated is True
    exact = markers(("step", "test"), *[("fail", f"case{i}") for i in range(MAX_FAIL_NAMES)])
    ok = outcome_for(running_job(), exact, started=STARTED, finished=FINISHED, rc=1)
    assert len(ok.fail_names) == MAX_FAIL_NAMES and ok.fail_truncated is False


# ══ B. DB v11 — `jobs.last_step` (§1.3) ══════════════════════════════════════


def test_a_fresh_database_is_at_the_newest_schema_with_last_step(store):
    """새 DB 는 마이그레이션을 건너뛰고 `_SCHEMA_V1` 만 실행한다 — 마이그레이션만 고치면
    새 DB 에 열이 없다. 그래서 10→11 만 보는 검사로는 이 버그를 못 잡는다."""
    assert DB_VERSION >= 12  # M5h 는 11(last_step) 과 12(대장) 둘을 더한다
    assert store.user_version() == DB_VERSION
    with raw(store) as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}
    assert "last_step" in cols
    assert "last_step" in _SCHEMA_V1  # 문면으로도 잠근다


def test_the_m5h_migrations_are_one_statement_per_tuple_entry():
    """`_MIGRATIONS` 는 문장 하나씩의 튜플이다 — `executescript` 가 아니다.

    번호는 밀린다: `dev` 가 v11(`failed_step_guessed`)을 먼저 가져가서 M5h 는 12~15 다.
    그래서 숫자가 아니라 **내용**으로 찾는다 — 다음에 또 밀려도 이 검사는 안 썩는다.
    """
    for target, stmts in _MIGRATIONS.items():
        assert isinstance(stmts, tuple) and stmts, target
        assert all(isinstance(s, str) for s in stmts), target
    blob = " ".join(s for stmts in _MIGRATIONS.values() for s in stmts)
    for needle in ("last_step", "job_failures", "fail_truncated", "jobs_key_finished"):
        assert needle in blob, needle


def test_an_old_database_migrates_through_the_m5h_steps_and_keeps_its_rows(tmp_path):
    """v10 에서 만든 DB 를 열면 기존 행이 살아남고 새 칸은 「모른다」로 시작한다."""
    path = tmp_path / "rcm.sqlite3"
    s = Store(path)
    j = enqueue(s)
    assert s.finish(j.id, SUCCEEDED, now=at(60), exit_code=0) is True  # 옛 방식 그대로
    s.close()
    conn = sqlite3.connect(path)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
        assert {"last_step", "fail_truncated"} <= cols  # 구현 전에는 여기서 빨갛다
        # v10 데이터베이스를 흉내 낸다: M5h 가 더한 것만 떼고 user_version 을 10 으로
        conn.execute("DROP INDEX IF EXISTS jobs_key_finished")
        conn.execute("DROP INDEX IF EXISTS job_failures_name")
        conn.execute("DROP TABLE IF EXISTS job_failures")
        conn.execute("ALTER TABLE jobs DROP COLUMN fail_truncated")
        conn.execute("ALTER TABLE jobs DROP COLUMN last_step")
        conn.execute("PRAGMA user_version=10")
        conn.commit()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 10
    finally:
        conn.close()
    s2 = Store(path)  # 10 → 11 → 12 가 여기서 돈다
    try:
        assert s2.user_version() == DB_VERSION and s2.healthy()
        got = s2.get_job(j.id)
        assert got is not None and got.state == SUCCEEDED and got.key == "gate"
        assert got.last_step is None  # 옛 잡은 「모른다」다 — 빈 문자열이 아니다
        assert got.fail_truncated is False
        with raw(s2) as c:
            tables = {r["name"] for r in c.execute("SELECT name FROM sqlite_master")}
        assert {"job_failures", "job_failures_name", "jobs_key_finished"} <= tables
        assert ledger(s2, j.id) == []
    finally:
        s2.close()
    s3 = Store(path)  # 두 번째 열기는 아무것도 바꾸지 않는다
    assert s3.user_version() == DB_VERSION and s3.get_job(j.id).key == "gate"
    s3.close()


def test_a_v11_database_gains_the_ledger_and_keeps_its_last_step(tmp_path):
    """v11 은 `last_step` 까지만 있다 — 12 가 대장을 얹으면서 그 값을 안 건드린다."""
    path = tmp_path / "rcm.sqlite3"
    s = Store(path)
    j = enqueue(s)
    assert s.finish(j.id, FAILED, now=at(60), exit_code=1, last_step=STEP_162) is True
    s.close()
    conn = sqlite3.connect(path)
    try:
        conn.execute("DROP INDEX IF EXISTS jobs_key_finished")
        conn.execute("DROP INDEX IF EXISTS job_failures_name")
        conn.execute("DROP TABLE IF EXISTS job_failures")
        conn.execute("ALTER TABLE jobs DROP COLUMN fail_truncated")
        conn.execute("PRAGMA user_version=11")
        conn.commit()
    finally:
        conn.close()
    s2 = Store(path)  # 11 → 12 만 돈다
    try:
        assert s2.user_version() == DB_VERSION
        got = s2.get_job(j.id)
        assert got.last_step == STEP_162 and got.fail_truncated is False
        assert ledger(s2, j.id) == []
    finally:
        s2.close()


def test_a_newer_database_is_refused(tmp_path):
    """v12 를 쓴 서버의 DB 를 옛 빌드가 열면 **거절**한다 — 조용히 열면 대장을 잃는다."""
    path = tmp_path / "rcm.sqlite3"
    Store(path).close()
    conn = sqlite3.connect(path)
    try:
        conn.execute(f"PRAGMA user_version={DB_VERSION + 1}")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(StoreError) as e:
        Store(path)
    assert "newer than this build" in str(e.value)


def test_last_step_round_trips_through_finish(store):
    """`finish(last_step=…)` → `jobs.last_step` → `Job.last_step`(§1.3)."""
    j = enqueue(store)
    assert store.finish(j.id, FAILED, now=at(60), exit_code=1, last_step=STEP_162) is True
    assert store.get_job(j.id).last_step == STEP_162
    other = enqueue(store, tree="b")
    assert store.finish(other.id, SUCCEEDED, now=at(61), exit_code=0) is True
    assert store.get_job(other.id).last_step is None  # 안 주면 「모른다」다


# ══ C. DB v12 — 실패 대장 쓰기·지우기 (§2.1) ═════════════════════════════════


def test_a_fresh_database_has_the_ledger_table_its_key_and_both_indexes(store):
    """표 하나 · 복합 PK · 인덱스 둘. PK 가 없으면 같은 이름이 두 번 들어간다."""
    with raw(store) as conn:
        cols = {r["name"]: r for r in conn.execute("PRAGMA table_info(job_failures)")}
        ledger_idx = {r["name"] for r in conn.execute("PRAGMA index_list(job_failures)")}
        jobs_idx = {r["name"] for r in conn.execute("PRAGMA index_list(jobs)")}
        job_cols = {r["name"]: r for r in conn.execute("PRAGMA table_info(jobs)")}
    assert set(cols) == {"job_id", "name", "seq"}
    assert int(cols["job_id"]["pk"]) == 1 and int(cols["name"]["pk"]) == 2
    assert int(cols["seq"]["pk"]) == 0
    assert all(int(cols[c]["notnull"]) == 1 for c in ("job_id", "name", "seq"))
    assert "job_failures_name" in ledger_idx
    assert "jobs_key_finished" in jobs_idx  # 창 질의가 이걸 탄다
    assert int(job_cols["fail_truncated"]["notnull"]) == 1
    assert str(job_cols["fail_truncated"]["dflt_value"]) == "0"
    assert "job_failures" in _SCHEMA_V1 and "fail_truncated" in _SCHEMA_V1


def test_finish_writes_the_names_it_was_given(store):
    """증거는 결과와 같은 트랜잭션에서 남는다 — `finish` 한 번이 둘을 같이 쓴다."""
    j = enqueue(store)
    ok = store.finish(
        j.id, FAILED, now=at(60), exit_code=1, failed_step="test", fail_names=["test", "lint"]
    )
    assert ok is True
    assert ledger(store, j.id) == ["test", "lint"]
    assert store.get_job(j.id).failed_step == "test"


def test_a_rejected_finish_leaves_no_ledger_rows(store):
    """이미 끝난 잡의 두 번째 `finish` 는 False 다 — 대장도 안 남는다(§2.1)."""
    j = enqueue(store)
    assert store.finish(j.id, SUCCEEDED, now=at(60), exit_code=0) is True
    assert store.finish(j.id, FAILED, now=at(70), exit_code=1, fail_names=["test"]) is False
    assert ledger(store, j.id) == []
    other = enqueue(store, tree="b")
    # `only_from` 이 안 맞아도 마찬가지다 — 거절은 아무 흔적도 안 남긴다
    assert (
        store.finish(
            other.id, FAILED, now=at(71), exit_code=1, only_from=[RUNNING], fail_names=["lint"]
        )
        is False
    )
    assert ledger(store, other.id) == []
    assert store.get_job(other.id).state == QUEUED


def test_a_failing_ledger_write_rolls_the_finish_back(store, tmp_path):
    """대장은 `finish` 와 **같은 트랜잭션**이다(§2.1 · mutcheck ⑲).

    커밋 뒤에 쓰면 잡은 이미 종료 상태로 남고 증거만 사라진다 — 그러면 「실패했는데 이름이
    없는 잡」이 조용히 늘고 `window_unnamed` 가 그것을 사실로 받아들인다. 대장 INSERT 를
    막는 트리거를 걸고, 잡이 **원래 상태로 돌아오는지**로 그 경계를 본다.
    """
    j = enqueue(store)
    with raw(store) as conn:
        names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master")}
        assert "job_failures" in names  # 구현 전에는 여기서 빨갛다
        conn.execute(
            "CREATE TRIGGER m5h_block_ledger BEFORE INSERT ON job_failures "
            "BEGIN SELECT RAISE(ABORT, 'blocked'); END"
        )
    with pytest.raises((sqlite3.Error, StoreError)):
        store.finish(j.id, FAILED, now=at(60), exit_code=1, fail_names=["test"])
    assert store.get_job(j.id).state == QUEUED  # 커밋 뒤에 썼다면 여기서 failed 다
    assert ledger(store, j.id) == []


def test_a_repeated_name_is_recorded_once(store):
    """PK `(job_id, name)` + `INSERT OR IGNORE` — 같은 이름을 두 번 찍어도 한 번이다."""
    j = enqueue(store)
    assert store.finish(j.id, FAILED, now=at(60), exit_code=1, fail_names=["a", "b", "a"]) is True
    assert ledger(store, j.id) == ["a", "b"]


def test_the_ledger_keeps_the_order_the_job_declared(store):
    """이름 순이 아니라 **잡이 찍은 순서**(seq)다(§2.2) — 표시가 그 순서를 그대로 쓴다."""
    j = enqueue(store)
    declared = ["zeta", "alpha", "mid"]
    assert store.finish(j.id, FAILED, now=at(60), exit_code=1, fail_names=declared) is True
    assert ledger(store, j.id) == declared
    # §2.1 — `seq` 는 **1부터**, 잡이 찍은 순서 그대로다
    rows = (
        store._conn()
        .execute("SELECT name, seq FROM job_failures WHERE job_id=? ORDER BY seq", (j.id,))
        .fetchall()
    )
    assert [int(r["seq"]) for r in rows] == [1, 2, 3]


def test_a_job_that_named_nothing_leaves_no_ledger_rows(store):
    """이름을 안 찍은 실패 잡은 대장에 행이 없다 — 그래야 `window_unnamed` 가 셀 수 있다."""
    j = enqueue(store)
    assert store.finish(j.id, FAILED, now=at(60), exit_code=1) is True
    assert ledger(store, j.id) == []


def test_fail_truncated_rides_on_the_job_row(store):
    """상한을 넘겨 버린 것이 있으면 잡 행이 그것을 밝힌다 — `GET /jobs/{id}` 가 이걸 싣는다."""
    j = enqueue(store)
    kw = {"fail_names": ["a"], "fail_truncated": True}
    assert store.finish(j.id, FAILED, now=at(60), exit_code=1, **kw) is True
    assert store.get_job(j.id).fail_truncated is True
    other = enqueue(store, tree="b")
    assert store.finish(other.id, FAILED, now=at(61), exit_code=1, fail_names=["a"]) is True
    assert store.get_job(other.id).fail_truncated is False


def test_delete_old_jobs_removes_the_ledger_with_the_job_row(store):
    """대장은 잡 행과 **같이** 지워진다(§2.1) — 남으면 고아 행이 이름 인덱스에 쌓인다."""
    old = finished(store, seconds=10, names=("test", "lint"))
    kept = finished(store, seconds=1000, names=("test",))
    store.mark_artifacts_purged([old, kept], at(2000))
    assert store.delete_old_jobs(at(500)) == 1  # old 만: purged 이고 finished_at < cutoff
    assert store.get_job(old) is None
    assert ledger(store, old) == []
    assert ledger(store, kept) == ["test"]


# ══ D. `failure_stats` — 창과 분모 (§2.1) ════════════════════════════════════


def test_the_window_is_the_last_n_terminal_jobs_of_the_same_key(store):
    """창은 같은 `key` 의 최근 종료 잡 N개다. 다섯 번 실패했어도 창이 셋이면 셋이다."""
    ids = [finished(store, seconds=10 * i, names=("test",)) for i in range(1, 6)]
    rows, window_jobs, unnamed = stats(store, ids[-1], window=3)
    assert window_jobs == 3
    assert seen_by_name(rows) == {"test": 3}
    assert unnamed == 0


def test_cancelled_and_lost_jobs_are_left_out_of_the_window(store):
    """취소·유실은 **아무 말도 안 한다** — 창에 들어가면 분모만 늘고 분자는 안 는다.

    mutcheck ⑱: 창의 `state IN (…)` 을 지우면 앵커 바로 밑의 취소·유실이 창을 채워
    `seen` 이 3 에서 **1** 로 떨어진다. 그래서 취소·유실을 **앵커와 옛 잡 사이**에 둔다.
    """
    a = finished(store, seconds=10, names=("test",))
    finished(store, seconds=20, names=("test",))
    finished(store, seconds=30, state=CANCELLED)
    finished(store, seconds=40, state=LOST)
    mine = finished(store, seconds=50, names=("test",))
    rows, window_jobs, unnamed = stats(store, mine, window=3)
    assert window_jobs == 3  # 취소·유실을 세면 여기도 3 이지만 seen 이 1 이 된다
    assert seen_by_name(rows) == {"test": 3}
    assert unnamed == 0
    assert {r.first_seen_job_id for r in rows} == {a}
    assert {r.last_seen_job_id for r in rows} == {mine}


def test_another_key_is_never_in_the_window(store):
    """창은 중앙값과 **같은 축**(`key`)을 쓴다 — 다른 프리셋의 실패는 남의 이력이다."""
    other = finished(store, seconds=10, key="deploy", names=("test",))
    mine = finished(store, seconds=20, names=("test",))
    rows, window_jobs, _ = stats(store, mine, window=20)
    assert window_jobs == 1
    assert seen_by_name(rows) == {"test": 1}
    assert other not in {r.first_seen_job_id for r in rows}


def test_a_job_outside_the_window_is_not_counted(store):
    """창 밖의 잡은 안 센다 — 「최근 N회 중 몇 번」의 N 이 흔들리면 판정이 흔들린다."""
    old = finished(store, seconds=10, names=("test",))
    for i in range(2, 6):
        finished(store, seconds=10 * i, names=("test",))
    newest = finished(store, seconds=60, names=("test",))
    rows, window_jobs, _ = stats(store, newest, window=3)
    assert window_jobs == 3
    assert seen_by_name(rows) == {"test": 3}
    assert old not in {r.first_seen_job_id for r in rows}


def test_window_unnamed_counts_failed_window_jobs_that_named_nothing(store):
    """분모의 품질을 같이 싣는다(결정 68). 성공한 잡은 「이름 없이 실패」가 **아니다**."""
    finished(store, seconds=10, names=("test",))
    finished(store, seconds=20, state=SUCCEEDED)  # 성공은 세지 않는다
    finished(store, seconds=30)  # 실패인데 이름이 없다
    mine = finished(store, seconds=40, names=("test",))
    rows, window_jobs, unnamed = stats(store, mine, window=20)
    assert window_jobs == 4
    assert unnamed == 1
    assert seen_by_name(rows) == {"test": 2}


def test_a_job_that_finished_after_this_one_is_not_in_the_window(store):
    """창은 **이 잡까지**다(§2.1 질의 ①) — 한 달 뒤에 다시 열어도 답이 같다."""
    old = finished(store, seconds=10, names=("test",))
    mine = finished(store, seconds=20, names=("test",))
    later = finished(store, seconds=30, names=("test",))
    rows, window_jobs, _ = stats(store, mine, window=20)
    assert window_jobs == 2  # 앵커가 없으면 3 이 되고 답이 시간에 따라 변한다
    assert seen_by_name(rows) == {"test": 2}
    assert [(r.first_seen_job_id, r.last_seen_job_id) for r in rows] == [(old, mine)]
    assert later not in {r.last_seen_job_id for r in rows}


def test_the_window_is_ordered_by_finished_at_then_id(store):
    """`ORDER BY finished_at DESC, id DESC` + 앵커의 `id <= :id` — 같은 시각의 동점 규칙."""
    first = finished(store, seconds=10, names=("test",))
    second = finished(store, seconds=10, names=("test",))
    assert second > first
    rows, window_jobs, _ = stats(store, second, window=1)
    assert window_jobs == 1
    assert seen_by_name(rows) == {"test": 1}
    assert [r.first_seen_job_id for r in rows] == [second]
    # 같은 시각의 앞 잡으로 물으면 뒤 잡은 창에 안 들어온다(`id <= :id`)
    rows, window_jobs, _ = stats(store, first, window=20)
    assert window_jobs == 1
    assert [r.last_seen_job_id for r in rows] == [first]


def test_first_and_last_seen_name_the_oldest_and_newest_in_the_window(store):
    """이름이 언제부터 언제까지 보였나 — 사람이 자기 변경과 맞대어 볼 유일한 단서다."""
    old = finished(store, seconds=10, names=("test",))
    finished(store, seconds=20)  # 이 잡은 그 이름을 안 찍었다
    new = finished(store, seconds=30, names=("test",))
    rows, window_jobs, unnamed = stats(store, new, window=20)
    assert window_jobs == 3 and unnamed == 1
    assert seen_by_name(rows) == {"test": 2}
    assert [(r.first_seen_job_id, r.last_seen_job_id) for r in rows] == [(old, new)]


def test_only_the_names_this_job_declared_come_back(store):
    """창 안의 다른 잡이 찍은 이름은 안 실린다 — 이 잡의 보고서다(§2.1 질의 ②)."""
    finished(store, seconds=10, names=("lint",))
    mine = finished(store, seconds=20, names=("test",))
    rows, _, _ = stats(store, mine, window=20)
    assert seen_by_name(rows) == {"test": 1}


def test_a_shallow_window_reports_the_jobs_it_actually_found(store):
    """창이 얕으면 **찾은 수**를 그대로 돌려준다 — 판정(`unknown`)이 그 수로 정해진다."""
    finished(store, seconds=10, names=("test",))
    mine = finished(store, seconds=20, names=("test",))
    rows, window_jobs, _ = stats(store, mine, window=20)
    assert window_jobs == 2  # 요청한 20 이 아니라 실제로 있는 2 다
    assert seen_by_name(rows) == {"test": 2}


def test_a_job_still_running_is_not_in_the_window(store):
    """`finished_at IS NOT NULL` — 아직 안 끝난 잡은 분모가 아니다."""
    mine = finished(store, seconds=10, names=("test",))
    enqueue(store, tree="live", now=at(20))
    assert store.claim(1, at(21)) is not None
    rows, window_jobs, unnamed = stats(store, mine, window=20)
    assert window_jobs == 1 and unnamed == 0
    assert seen_by_name(rows) == {"test": 1}


def test_a_job_that_named_nothing_gets_no_rows_but_still_reports_the_window(store):
    """이름이 없으면 행도 없다 — 그래도 창은 재서 돌려준다(화면이 「모른다」를 그린다)."""
    finished(store, seconds=10, names=("test",))
    mine = finished(store, seconds=20)
    rows, window_jobs, unnamed = stats(store, mine, window=20)
    assert rows == []
    assert window_jobs == 2 and unnamed == 1


# ── 검증 라운드가 뮤테이션으로 증명한 구멍 ────────────────────────────────────


def test_a_cancelled_job_that_overflowed_the_cap_is_not_marked_truncated():
    """§1.2 「네 값은 늘 같이 움직인다」 — 이름을 안 남기는 잡에 「잘렸다」고 말할 자리가 없다."""
    markers_over = markers(("step", "test"), *(("fail", f"n{i}") for i in range(150)))
    for forced, state in (("cancelled", CANCELLED), ("lost", LOST)):
        oc = outcome_for(
            running_job(),
            markers_over,
            started=STARTED,
            finished=FINISHED,
            rc=-15,
            cancelled=forced == "cancelled",
            lost=forced == "lost",
        )
        assert oc.state == state
        assert oc.fail_names == () and oc.fail_truncated is False, forced
        assert oc.last_step is None and oc.failed_step is None, forced
    ok = outcome_for(running_job(), markers_over, started=STARTED, finished=FINISHED, rc=0)
    assert ok.state == SUCCEEDED and ok.fail_names == () and ok.fail_truncated is False


def test_a_forced_end_does_not_close_the_open_step_as_ok():
    """§1.2 — 강제 종료는 `exit_code=None` 으로 판다. `rc` 를 그대로 넘기면 프로세스가 0 으로
    빠져나간 취소 잡의 마지막 스텝이 「성공」으로 굳는다."""
    oc = outcome_for(
        running_job(),
        markers(("step", "build")),
        started=STARTED,
        finished=FINISHED,
        rc=0,
        cancelled=True,
    )
    assert oc.state == CANCELLED and oc.last_step is None


def test_a_nameless_timed_out_job_counts_in_the_unnamed_denominator(store):
    """§2.1 — 분모의 품질은 `failed` 만이 아니라 `timed_out` 도 센다(창에 드는 상태 셋 중 둘)."""
    finished(store, seconds=10, state=TIMED_OUT)  # 이름을 안 남긴 시간 초과 잡
    mine = finished(store, seconds=60, names=("test",))
    rows, window_jobs, unnamed = stats(store, mine)
    assert window_jobs == 2 and unnamed == 1
    assert [r.seen for r in rows] == [1]


def test_the_upgrade_moves_an_old_failed_label_to_the_last_step_column(tmp_path):
    """v14 — 신고자가 #162 를 다시 열면 「고쳤다는 그 문자열」을 보면 안 된다(검증 1).

    옛 행의 라벨이 선언이었는지 추론이었는지는 이제 와서 구분할 수 없다. 그래서 인과를
    주장하는 칸에서 자리만 말하는 칸으로 **옮긴다**.
    """
    path = tmp_path / "old.sqlite3"
    store = Store(path)
    # 새 코드가 쓴 행 — 선언 라벨은 언제나 대장(`job_failures`) 행과 함께 쓰인다(v16 은 대장 없는
    # 라벨만 옛 빌드의 추론으로 본다)
    kept = finished(store, seconds=10, failed_step="test", last_step="test", names=("test",))
    moved = finished(store, seconds=20, failed_step=STEP_162)
    cancelled = finished(store, seconds=30, state=CANCELLED, failed_step=STEP_162)
    store.close()

    raw = sqlite3.connect(path)
    try:  # M5h 이전처럼 되돌린다: 라벨은 failed_step 에만 있고 last_step 은 비어 있다
        raw.execute("UPDATE jobs SET last_step=NULL WHERE id IN (?,?)", (moved, cancelled))
        raw.execute("UPDATE jobs SET failed_step=? WHERE id=?", (STEP_162, moved))
        raw.execute("PRAGMA user_version=12")
        raw.commit()
    finally:
        raw.close()

    store = Store(path)
    assert store.user_version() == DB_VERSION
    old_failed = store.get_job(moved)
    assert old_failed is not None
    assert old_failed.failed_step is None and old_failed.last_step == STEP_162
    gone = store.get_job(cancelled)
    assert gone is not None and gone.failed_step is None and gone.last_step is None
    fresh = store.get_job(kept)  # 새 코드가 쓴 행은 안 건드린다(둘 다 있었다)
    assert fresh is not None and fresh.failed_step == "test" and fresh.last_step == "test"
    store.close()


def test_adding_a_column_that_is_already_there_is_not_fatal(tmp_path):
    """번호가 밀린 열 추가 마이그레이션은 이미 있는 열을 만나도 죽으면 안 된다.

    dev 의 `tests/test_failed_step_guess.py` 가 이 성질을 지키고 있었다(그 파일은 M5h 가
    대체한 설계를 잠그고 있어 사라진다). M5h 의 `last_step` 이 정확히 같은 처지를 겪었다 —
    `dev` 가 v11 을 먼저 가져가서 v12 로 밀렸다. 옛 빌드로 한 번이라도 연 DB 는 열은 있는데
    버전이 낮고, 그대로 두면 `ADD COLUMN` 이 「duplicate column name」으로 죽는다. 버전이
    안 올라가니 **다음에도 똑같이 죽어** 서버가 영영 안 뜬다.
    """
    path = tmp_path / "rcm.sqlite3"
    store = Store(path)
    jid = finished(store, seconds=10, last_step="build web", names=("test",))
    store.close()

    raw = sqlite3.connect(path)
    try:  # 리베이스 전 빌드가 남긴 모양: 열은 있는데 버전은 낮다
        raw.execute("PRAGMA user_version=11")
        raw.commit()
        cols = {r[1] for r in raw.execute("PRAGMA table_info(jobs)")}
        assert "last_step" in cols and "fail_truncated" in cols
    finally:
        raw.close()

    store = Store(path)  # 여기서 죽으면 안 된다
    try:
        assert store.user_version() == DB_VERSION and store.healthy()
        got = store.get_job(jid)
        assert got is not None and got.last_step == "build web"  # 값도 그대로
        again = Store(path)  # 다시 열어도 조용하다
        assert again.user_version() == DB_VERSION
        again.close()
    finally:
        store.close()
