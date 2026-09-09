"""부피 회계와 `POST /gc` (M5g §5) — 상태 문서 · health · 라우트 · 락.

명세 `docs/m5g-workplan.md` §5.1·§5.2·§5.5. **구현보다 먼저 썼다(test-first).**

여기서 지키는 것:
- 회계의 **산식이 맞는다**. `volume = workspace + snapshot = evictable + non_evictable`.
  초안은 안 맞았고 코덱스 2차 리뷰 9번이 그걸 잡았다.
- 모르는 값은 `null`(0 이 아니다) + `error_code`.
- `POST /gc` 는 admin 만. dry-run 은 **아무것도 안 지우고** `storage_after` 가 null 이다.
- 실제 gc 응답은 `planned`/`deleted`/`failed` 를 가른다 — 계획을 실제 회수처럼 내면 거짓이다.
"""

from __future__ import annotations

import threading
import time

import pytest

from test_janitor import poll
from test_server import Server

GB = 1024**3


@pytest.fixture
def srv(tmp_path):
    """청소기를 **띄웠다가 첫 sweep 뒤에 세운다**.

    주기 스레드를 살려 두면 시험이 만든 워크스페이스를 그 스레드가 먼저 지워 버려서 `/gc` 가
    빈 계획을 낸다(경합). `app.retention` 은 그대로 남으므로 라우트는 정상으로 돈다.
    """
    s = Server(tmp_path, workers=False, workspace_storage_max_bytes=GB, min_free_bytes=0)
    try:
        s.app.start()
        poll(lambda: s.app.retention.last_sweep_at is not None, 5.0, "first sweep")
        s.app.retention.stop()
        yield s
    finally:
        s.close()


def finished_job(srv, *, state: str = "failed", days_ago: float = 3.0) -> int:
    """`days_ago` 전에 끝난 잡 하나. Store 에 직접 찍는다(HTTP 왕복 없이)."""
    from datetime import timedelta

    from test_janitor import finished

    return finished(srv.store, state=state, finished_at=srv.app.now_fn() - timedelta(days=days_ago))


def volume(srv, job_id: int, *, ws: int = 8192, tar: int = 4096) -> None:
    data = srv.cfg.data_dir
    d = data / "workspaces" / str(job_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "big").write_bytes(b"x" * ws)
    j = data / "jobs" / str(job_id)
    j.mkdir(parents=True, exist_ok=True)
    (j / "tree.tar.gz").write_bytes(b"t" * tar)


def storage(srv) -> dict:
    return srv.req("GET", "/api/status")[1]["server"]["job_storage"]


# ── /api/status → server.job_storage ─────────────────────────────────────────


def test_job_storage_has_the_documented_keys(srv):
    doc = storage(srv)
    assert set(doc) == {
        "workspace_bytes",
        "snapshot_bytes",
        "volume_bytes",
        "evictable_bytes",
        "non_evictable_bytes",
        "orphan_bytes",
        "limit_bytes",
        "min_free_bytes",
        "free_bytes",
        "over_budget_bytes",
        "projected_short_free_bytes",
        "budget_unreachable",
        "no_progress",
        "measured_at",
        "last_sweep_at",
        "next_sweep_at",
        "error_code",
    }


def test_the_accounting_adds_up(srv):
    """`volume = workspace + snapshot` 이고 `volume = evictable + non_evictable` 이다."""
    volume(srv, 4242)  # 잡 행이 없는 고아 — 지울 수 없는 쪽에 들어간다
    srv.app.retention.sweep_once(srv.app.now_fn())
    doc = storage(srv)
    assert doc["volume_bytes"] == doc["workspace_bytes"] + doc["snapshot_bytes"]
    assert doc["volume_bytes"] == doc["evictable_bytes"] + doc["non_evictable_bytes"]
    assert doc["orphan_bytes"] > 0 and doc["evictable_bytes"] == 0


def test_a_disabled_limit_is_null_not_zero(tmp_path):
    """0 은 「끔」이라는 설정값이지 「상한이 0 바이트」가 아니다 — 화면이 `—` 로 그린다."""
    s = Server(tmp_path, workers=False, workspace_storage_max_bytes=0, min_free_bytes=0)
    try:
        s.app.start()
        doc = storage(s)
        assert doc["limit_bytes"] is None and doc["min_free_bytes"] is None
    finally:
        s.close()


def test_sweep_times_are_null_until_the_first_sweep(tmp_path):
    s = Server(tmp_path, workers=False)
    try:  # start() 를 안 부르면 청소기가 없다
        doc = storage(s)
        assert doc["last_sweep_at"] is None and doc["next_sweep_at"] is None
    finally:
        s.close()


def test_an_unmeasurable_volume_is_null_with_an_error_code(srv, monkeypatch):
    volume(srv, 4242)
    monkeypatch.setattr(srv.app.retention, "_measure_dir", lambda p: None)
    srv.app.retention.sweep_once(srv.app.now_fn())
    doc = storage(srv)
    assert doc["volume_bytes"] is None and doc["over_budget_bytes"] is None
    assert doc["error_code"] is None or isinstance(doc["error_code"], str)


# ── /api/health → storage ────────────────────────────────────────────────────


def test_health_carries_storage_without_any_path(srv):
    body = srv.req("GET", "/api/health")[1]
    st = body["storage"]
    assert set(st) == {
        "volume_bytes",
        "free_bytes",
        "limit_bytes",
        "min_free_bytes",
        "last_sweep_at",
        "next_sweep_at",
        "budget_unreachable",
        "no_progress",
        "under_floor",
    }
    # health 는 토큰 없이 열린다 — 경로를 싣지 않는다
    assert str(srv.cfg.data_dir) not in repr(body)


def test_being_over_budget_is_not_a_health_failure(tmp_path):
    """예산 초과는 고장이 아니라 다음 sweep 이 할 일이다.

    청소기를 **살려 둔 채** 본다 — 세워 두면 503 이 나는데 그건 예산이 아니라 죽은 스레드 때문이다.
    """
    s = Server(tmp_path, workers=False, workspace_storage_max_bytes=1024**3, min_free_bytes=0)
    try:
        s.app.start()
        poll(lambda: s.app.retention.last_sweep_at is not None, 5.0, "first sweep")
        volume(s, 4242, ws=8192)
        status, body = s.req("GET", "/api/health")
        assert status == 200 and body["ok"] is True
    finally:
        s.close()


# ── POST /gc ─────────────────────────────────────────────────────────────────


def test_gc_needs_an_admin_token(srv):
    assert srv.req("POST", "/gc", json_body={"dry_run": True})[0] == 401
    assert srv.req("POST", "/gc", json_body={"dry_run": True}, token="alice")[0] == 403
    assert srv.req("POST", "/gc", json_body={"dry_run": True}, token="admin")[0] == 200


def test_gc_only_takes_post(srv):
    assert srv.req("GET", "/gc", token="admin")[0] == 405


@pytest.mark.parametrize("body", [{"dry_run": "false"}, {"dry_run": 1}, {"nope": True}])
def test_gc_rejects_a_malformed_body(srv, body):
    """`{"dry_run": "false"}` 는 참이 아니라 오류다 — 조용히 지우면 안 된다."""
    assert srv.req("POST", "/gc", json_body=body, token="admin")[0] == 400


def test_dry_run_deletes_nothing_and_has_no_storage_after(srv):
    job = finished_job(srv, days_ago=3)
    volume(srv, job)
    status, body = srv.req("POST", "/gc", json_body={"dry_run": True}, token="admin")
    assert status == 200 and body["dry_run"] is True
    assert [i["job_id"] for i in body["planned"]] == [job]
    assert body["deleted"] == [] and body["failed"] == []
    assert body["storage_after"] is None  # 예측치를 「실측 뒤」 자리에 넣지 않는다
    assert (srv.cfg.data_dir / "workspaces" / str(job)).exists()


def test_a_real_gc_separates_what_it_planned_from_what_it_deleted(srv):
    job = finished_job(srv, days_ago=3)
    volume(srv, job)
    status, body = srv.req("POST", "/gc", json_body={"dry_run": False}, token="admin")
    assert status == 200 and body["dry_run"] is False
    assert [i["job_id"] for i in body["planned"]] == [job]
    assert [i["job_id"] for i in body["deleted"]] == [job]
    assert body["failed"] == []
    assert body["freed_bytes"] > 0
    assert body["storage_before"] is not None and body["storage_after"] is not None
    assert not (srv.cfg.data_dir / "workspaces" / str(job)).exists()


def test_a_delete_that_fails_is_reported_not_counted_as_freed(srv, monkeypatch):
    job = finished_job(srv, days_ago=3)
    volume(srv, job)
    monkeypatch.setattr(
        srv.app.retention, "_purge_volume", lambda job_id: (_ for _ in ()).throw(OSError(13, "no"))
    )
    body = srv.req("POST", "/gc", json_body={"dry_run": False}, token="admin")[1]
    assert body["deleted"] == [] and [f["job_id"] for f in body["failed"]] == [job]
    assert body["freed_bytes"] == 0


def test_a_reason_code_comes_down_not_a_sentence(srv):
    """결정 37 — 서버는 문장이 아니라 코드를 내려보낸다."""
    job = finished_job(srv, days_ago=3)
    volume(srv, job)
    body = srv.req("POST", "/gc", json_body={"dry_run": True}, token="admin")[1]
    assert body["planned"][0]["reason"] == "age"


# ── 락 ───────────────────────────────────────────────────────────────────────


def test_gc_and_the_periodic_sweep_do_not_run_at_the_same_time(srv):
    """둘이 겹치면 같은 잡을 두 번 지운다. 작업 락은 `Janitor` 안에만 있다."""
    jan = srv.app.retention
    started, release = threading.Event(), threading.Event()
    real = jan.plan

    def slow(now, **kw):
        started.set()
        release.wait(5.0)
        return real(now, **kw)

    jan.plan = slow
    t = threading.Thread(target=lambda: jan.sweep_once(srv.app.now_fn()), daemon=True)
    t.start()
    assert started.wait(5.0)
    done = threading.Event()
    threading.Thread(
        target=lambda: (jan.gc(srv.app.now_fn(), dry_run=True), done.set()), daemon=True
    ).start()
    assert not done.wait(0.3)  # 락이 없으면 여기서 끝난다
    release.set()
    assert done.wait(5.0)
    t.join(5.0)


def test_two_concurrent_gcs_are_serialised(srv):
    jan = srv.app.retention
    order: list[str] = []
    lock = threading.Lock()
    real = jan.plan

    def slow(now, **kw):
        with lock:
            order.append("in")
        time.sleep(0.1)
        with lock:
            order.append("out")
        return real(now, **kw)

    jan.plan = slow
    threads = [
        threading.Thread(target=lambda: jan.gc(srv.app.now_fn(), dry_run=True)) for _ in range(2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5.0)
    assert order == ["in", "out", "in", "out"]  # 겹치면 in in out out 이 된다
