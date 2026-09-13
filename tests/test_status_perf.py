"""`/api/status` 가 보존된 잡 수에 끌려가지 않는다 (M5f 결정 49).

재 본 것: 중앙값 계산이 요청의 91~93% 였다. 45일치 완료 잡을 **완전한 `Job` 객체**로 만들어
(행마다 서브쿼리 둘 + JSON 파싱 둘) `medians_from` 에 넘기는데, 정작 중앙값이 읽는 것은
`key · pool · state · created_at · started_at · finished_at` 여섯 개뿐이다. 1만 행에서 168 ms,
5만 행에서 1.2초 — 그리고 `_snapshot` 캐시는 TTL 이 0.2초에 마커 줄마다 무효화돼 실질적으로
안 듣는다.

여기서는 **타이밍이 아니라 구조**를 잠근다(타이밍 단언은 CI 에서 흔들린다):
- 표본 읽기가 무거운 `Job` 을 안 만든다 — 행당 서브쿼리가 0 이다.
- 중앙값은 잡이 **끝났을 때만** 다시 계산한다. 마커 한 줄이 45일치를 다시 읽게 하면 안 된다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from remote_ci_monitor.core.model import QUEUED, SUCCEEDED, Requester, Source
from remote_ci_monitor.core.queue import join_key, medians_from
from remote_ci_monitor.store import Store

NOW = datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC)
ALICE = Requester(name="alice-laptop", label="alice@laptop")


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "rcm.sqlite3")
    yield s
    s.close()


def finished(store: Store, n: int, *, key: str = "gate:full") -> None:
    for i in range(n):
        src = Source(mode="tree", repo="org/app", tree_hash=f"{i:04x}")
        j = store.create_job(
            preset=key.split(":")[0],
            inputs={},
            key=key,
            concurrency_group=None,
            source=src,
            requester=ALICE,
            timeout_seconds=1200,
            join_key=join_key(key, {}, f"{i:04x}"),
            now=NOW - timedelta(days=1, seconds=i),
            state=QUEUED,
        )
        store.claim(1, NOW - timedelta(days=1, seconds=i - 1))
        store.finish(j.id, SUCCEEDED, now=NOW - timedelta(days=1, seconds=i - 401), exit_code=0)


def count_statements(store: Store) -> list[str]:
    seen: list[str] = []
    store._conn().set_trace_callback(seen.append)
    return seen


def test_reading_samples_does_not_hydrate_a_job_per_row(store):
    """행마다 joiners·events 서브쿼리를 도는 것이 요청 비용의 대부분이었다."""
    finished(store, 5)
    seen = count_statements(store)
    rows = store.list_sample_rows(NOW - timedelta(days=45))
    store._conn().set_trace_callback(None)
    assert len(rows) == 5
    assert len(seen) == 1, seen  # 행 수와 무관하게 **한 문장**


def test_sample_rows_carry_exactly_what_the_median_needs(store):
    finished(store, 3)
    rows = store.list_sample_rows(NOW - timedelta(days=45))
    cfg_fields = {"key", "pool", "state", "created_at", "started_at", "finished_at"}
    assert cfg_fields <= set(vars(rows[0]))


def test_medians_from_accepts_the_lean_rows(store):
    """순수 계층은 무엇을 받는지 모른다 — 여섯 필드만 읽는다."""
    from remote_ci_monitor.core.queue import QueueConfig

    finished(store, 4)
    rows = store.list_sample_rows(NOW - timedelta(days=45))
    out = medians_from(rows, NOW, QueueConfig())
    assert out["gate:full"].sample_count == 4


# ── 중앙값은 잡이 끝났을 때만 다시 잰다 ────────────────────────────────────


def app_for(tmp_path):
    from test_server import Server

    return Server(tmp_path, workers=False)


def test_a_marker_line_does_not_invalidate_the_medians(tmp_path):
    """45일치 중앙값이 `::rcm::step::` 한 줄 때문에 다시 계산되면 안 된다."""
    srv = app_for(tmp_path)
    try:
        app = srv.app
        app._snapshot()  # 한 번 데운다
        before = app._snapshot().medians
        app._on_marker(1, "step", "build")
        assert app._snapshot().medians is before  # 같은 객체 — 다시 안 읽었다
    finally:
        srv.close()


def test_finishing_a_job_does_invalidate_the_medians(tmp_path):
    """새 표본이 생겼으면 다음 요청은 새로 재야 한다.

    스냅샷 자체의 무효화(`_mark_dirty`)와는 별개다 — 마커 한 줄은 스냅샷을 다시 읽게 하지만
    중앙값은 그대로 쓰고, 잡이 끝나야 중앙값도 다시 잰다.
    """
    srv = app_for(tmp_path)
    try:
        app = srv.app
        app._snapshot()
        before = app._snapshot().medians
        app._mark_medians_dirty()
        app._mark_dirty()  # 잡이 끝나면 둘 다 무효가 된다
        assert app._snapshot().medians is not before
    finally:
        srv.close()


# ── 원격 워커 레인은 한 번에 읽는다 ────────────────────────────────────────


def test_worker_lanes_are_read_in_one_statement(tmp_path):
    """워커마다 `jobs_of_worker` 를 돌면 마커 한 줄에 SQL 이 수백 개가 된다.

    실측: 워커 50 · 실행 250 에서 마커 한 줄에 555 문장, 5.25 ms. 게다가 그 잡들은 이미
    스냅샷에 있고, `WorkerInfo` 가 쓰는 것은 `id`·`lane`·`started_at` 셋뿐이다.
    """
    from test_worker_api import WorkerServer

    srv = WorkerServer(tmp_path, admission="always")
    try:
        for i, name in enumerate(("build-02", "build-03", "lin-01")):  # 픽스처가 만든 셋
            srv.registered(name, lanes=2)
            jid = srv.queued_job(tree_hash=f"{i:02x}" * 32)
            assert srv.claimed(name) == jid
        seen = count_statements(srv.app.store)
        srv.app.all_worker_infos(srv.app.now_fn())
        srv.app.store._conn().set_trace_callback(None)
        selects = [s for s in seen if s.strip().upper().startswith("SELECT")]
        # 워커 목록 하나 + 활성 레인 하나. 워커 수와 무관하다.
        assert len(selects) <= 2, selects
    finally:
        srv.close()


def test_a_busy_remote_lane_still_reports_its_job_and_start_time(tmp_path):
    """빠르게 읽는다고 화면이 덜 알면 안 된다."""
    from test_worker_api import WorkerServer

    srv = WorkerServer(tmp_path, admission="always")
    try:
        srv.registered("build-02", lanes=2)
        jid = srv.queued_job()
        assert srv.claimed("build-02") == jid
        lane = srv.worker_lane("build-02", 1)
        assert lane["state"] == "busy" and lane["job_id"] == jid and lane["since"]
        assert srv.worker_lane("build-02", 2)["state"] == "idle"
    finally:
        srv.close()


# ── 은퇴한 워커는 잊는다 ────────────────────────────────────────────────────


def test_a_worker_gone_for_a_long_time_is_forgotten(store):
    """지우는 코드가 없어서 은퇴한 워커가 영원히 남았다 — 200대면 `down` 레인 401개가
    매 요청에 붙는다. 잡을 잃지 않으려면 **활성 잡이 없을 때만** 지운다."""
    old = NOW - timedelta(days=30)
    store.register_worker("gone", pool="linux", lanes=2, host_name="x", version="1", now=old)
    store.register_worker("here", pool="linux", lanes=2, host_name="y", version="1", now=NOW)
    assert store.forget_workers(NOW - timedelta(days=7)) == ["gone"]
    assert [w.name for w in store.list_workers()] == ["here"]


def test_a_forgotten_worker_never_takes_a_job_with_it(store):
    """활성 잡이 있는 워커는 아무리 오래됐어도 안 지운다 — 그 잡이 큐에서 사라진다."""
    old = NOW - timedelta(days=30)
    store.register_worker("busy-one", pool="default", lanes=1, host_name="x", version="1", now=old)
    src = Source(mode="tree", repo="org/app", tree_hash="9f8e")
    store.create_job(
        preset="gate",
        inputs={},
        key="gate:full",
        concurrency_group=None,
        source=src,
        requester=ALICE,
        timeout_seconds=1200,
        join_key=join_key("gate:full", {}, "9f8e"),
        now=old,
        state=QUEUED,
    )
    store.claim(1, old, worker_name="busy-one")
    assert store.forget_workers(NOW - timedelta(days=7)) == []
    assert [w.name for w in store.list_workers()] == ["busy-one"]


# ── 실패는 캐시하지 않는다 ──────────────────────────────────────────────────


def test_a_transient_read_failure_is_retried_on_the_next_request(tmp_path):
    """중앙값 읽기가 한 번 실패했다고 그 결과를 캐시하면, SQLite 가 잠깐 잠긴 것만으로
    `medians: null` 이 다음 잡이 끝날 때까지 모든 상태 문서에 박힌다 — 한가한 서버면 몇 시간이다."""
    srv = app_for(tmp_path)
    try:
        app = srv.app
        real = app.store.list_sample_rows
        calls: list[int] = []

        def flaky(since):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("database is locked")
            return real(since)

        app.store.list_sample_rows = flaky
        assert app._snapshot().medians_error is not None  # 첫 요청은 실패한다
        app._mark_dirty()
        assert app._snapshot().medians_error is None  # 다음 요청은 **다시 시도한다**
        assert len(calls) == 2
    finally:
        srv.close()


def test_the_median_window_cannot_drift_forever_on_a_quiet_server(tmp_path):
    """잡이 하나도 안 끝나는 동안에도 45일 창은 흘러간다 — 시간으로도 상한을 둔다."""
    import time as _time

    from remote_ci_monitor.server import MEDIANS_MAX_AGE_SECONDS

    srv = app_for(tmp_path)
    try:
        app = srv.app
        app._snapshot()
        before = app._snapshot().medians
        app._medians_loaded_at -= MEDIANS_MAX_AGE_SECONDS + 1
        app._mark_dirty()
        assert app._snapshot().medians is not before
        assert MEDIANS_MAX_AGE_SECONDS <= 3600  # 창이 한 시간 넘게 굳으면 안 된다
        assert _time is not None
    finally:
        srv.close()


def test_recent_jobs_do_not_scan_every_terminal_row(store):
    """`list_recent(8)` 이 `/api/status` 의 새 지배항이었다 — 5만 행에서 7.1 ms.

    `state IN (...)` 때문에 `jobs_finished` 를 못 타고 종료 잡 전체를 정렬한다. 여덟 개를
    고르려고 5만 개를 줄 세우는 셈이다. 커버링 인덱스면 0.009 ms 다(790배).
    """
    finished(store, 3)
    plan = " | ".join(
        r[3]
        for r in store._conn().execute(
            "EXPLAIN QUERY PLAN SELECT id FROM jobs WHERE state IN "
            "('cancelled','failed','lost','succeeded','timed_out') "
            "ORDER BY finished_at DESC, id DESC LIMIT 8"
        )
    )
    assert "jobs_recent" in plan, plan


def test_recent_still_returns_the_newest_first(store):
    """빠르게 고른다고 순서가 틀리면 안 된다."""
    finished(store, 5)
    rows = store.list_recent(3)
    assert len(rows) == 3
    assert [r.finished_at for r in rows] == sorted((r.finished_at for r in rows), reverse=True)


# ── 검증에서 나온 것들 ──────────────────────────────────────────────────────


def test_a_worker_cleanup_failure_does_not_kill_the_janitor(tmp_path):
    """`_errname` 은 OSError 전용이다(`e.errno`). SQLite 오류를 그걸로 포맷하면 AttributeError 가
    나고 janitor 스레드가 죽어 보존 정리가 **영구히** 멈춘다 — /api/health 는 503 이 된다."""
    import sqlite3

    from remote_ci_monitor.config import ServerConfig
    from remote_ci_monitor.janitor import Janitor

    cfg = ServerConfig()
    cfg.server.data_dir = str(tmp_path)
    store = Store(tmp_path / "rcm.sqlite3")
    errors: list[str] = []

    def boom(cutoff):
        raise sqlite3.OperationalError("database is locked")

    store.forget_workers = boom
    j = Janitor(store, cfg, now_fn=lambda: NOW, on_error=errors.append, log=lambda m: None)
    try:
        j.sweep_once()  # 죽지 않아야 한다
    finally:
        store.close()
    assert errors and "OperationalError" in errors[0], errors


def test_a_lane_query_failure_never_claims_the_lane_is_idle(tmp_path):
    """조회가 실패하면 「busy 를 모르는」 것이지 「안 바쁜」 것이 아니다.

    `idle` 로 그리면 `since` 까지 등록 시각으로 바뀌어, 화면이 「그 레인은 등록 이후 계속
    놀았다」고 **없는 사실을 지어낸다**. 집안 규칙은 fail-open 금지다.
    """
    import sqlite3

    from test_worker_api import WorkerServer

    srv = WorkerServer(tmp_path, admission="always")
    try:
        srv.registered("build-02", lanes=1)
        jid = srv.queued_job()
        assert srv.claimed("build-02") == jid
        assert srv.worker_lane("build-02", 1)["state"] == "busy"

        def boom():
            raise sqlite3.OperationalError("database is locked")

        srv.app.store.active_worker_lanes = boom
        status, body = srv.req("GET", "/api/status", token="admin")
        # 무엇이 되든 「idle」은 아니다 — 실패는 실패로 드러난다
        if status == 200:
            lanes = [w for w in body["server"]["workers"] if w.get("worker") == "build-02"]
            assert not lanes or lanes[0]["state"] != "idle", lanes
        else:
            assert status >= 500
    finally:
        srv.close()


def test_forgetting_a_worker_does_not_silence_the_dead_pool_alarm(store):
    """마지막 워커를 잊으면 `pools_without_workers` 가 비어 `rcm check` 가 PASS 로 바뀐다 —
    그 풀에 잡이 아직 기다리고 있는데도."""
    old = NOW - timedelta(days=30)
    store.register_worker("lin-01", pool="linux", lanes=1, host_name="x", version="1", now=old)
    src = Source(mode="tree", repo="org/app", tree_hash="9f8e")
    store.create_job(
        preset="gate",
        inputs={},
        key="gate:full",
        concurrency_group=None,
        source=src,
        requester=ALICE,
        timeout_seconds=1200,
        join_key=join_key("gate:full", {}, "9f8e"),
        now=old,
        state=QUEUED,
        pool="linux",
    )
    assert store.forget_workers(NOW - timedelta(days=7)) == []
    assert [w.name for w in store.list_workers()] == ["lin-01"]
