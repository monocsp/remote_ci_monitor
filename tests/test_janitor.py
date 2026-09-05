"""보존 정리(janitor, M3) — 기간 지난 잡의 `jobs/<id>/`·`workspaces/<id>/` 삭제 · DB 표시 ·
활성 잡 보호 · 삭제 실패 보고와 재시도 · idempotent · `mirrors/` 보존 · 메타데이터 보존 기간 ·
로그 404 `log expired` · 스레드 죽음/정체 → `last_error`/`/api/health` 503.
명세는 docs/m3-workplan.md §2.

시각은 고정 NOW 기준으로 Store 에 직접 찍고 `sweep_once(now)` 로 돌린다(스레드 없음). 스레드가
필요한 것(`start()`·서버 wiring)만 마감 있는 폴링으로 본다.
"""

import errno
import itertools
import json
import shutil
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from remote_ci_monitor.core.model import (
    CANCELLING,
    FAILED,
    LOST,
    QUEUED,
    RUNNING,
    SUCCEEDED,
    TIMED_OUT,
    Requester,
    Source,
)
from remote_ci_monitor.core.queue import join_key
from remote_ci_monitor.janitor import Janitor
from remote_ci_monitor.store import Store
from test_server import Server
from test_worker import make_config

NOW = datetime(2026, 9, 5, 12, 0, 0, tzinfo=UTC)
DAY = timedelta(days=1)
ALICE = Requester(name="alice-laptop", label="alice@laptop")
_seq = itertools.count(1)


# ── 도우미 ───────────────────────────────────────────────────────────────────


def new_job(store: Store, *, created: datetime, preset: str = "ok", state: str = QUEUED):
    tree = f"tree-{next(_seq)}"  # 합류 키가 겹치지 않게
    return store.create_job(
        preset=preset,
        inputs={},
        key=preset,
        concurrency_group=None,
        source=Source(mode="tree", repo="org/app", base_sha="abc", dirty=False, tree_hash=tree),
        requester=ALICE,
        timeout_seconds=60,
        join_key=join_key(preset, {}, tree),
        now=created,
        state=state,
    )


def finished(
    store: Store, *, state: str = SUCCEEDED, finished_at: datetime, created: datetime | None = None
) -> int:
    """enqueue → claim → finish 를 명시적 시각으로. 다른 queued 잡이 없을 때 부른다."""
    created = created or finished_at - timedelta(minutes=5)
    j = new_job(store, created=created)
    claimed = store.claim(1, created + timedelta(seconds=1))
    assert claimed is not None and claimed.id == j.id
    exit_code = 0 if state == SUCCEEDED else (None if state == LOST else 1)
    store.finish(j.id, state, now=finished_at, exit_code=exit_code, summary=state)
    return j.id


def touch_dirs(cfg, job_id: int) -> tuple[Path, Path]:
    job_dir = cfg.data_dir / "jobs" / str(job_id)
    ws = cfg.data_dir / "workspaces" / str(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "log.txt").write_text("line\n")
    (ws / "sub").mkdir(parents=True, exist_ok=True)
    (ws / "x").write_text("x")
    (ws / "sub" / "y").write_text("y")
    return job_dir, ws


class Recorder:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.logs: list[str] = []

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def log(self, msg: str) -> None:
        self.logs.append(msg)


def make_janitor(store: Store, cfg, *, now: datetime = NOW) -> tuple[Janitor, Recorder]:
    rec = Recorder()
    jan = Janitor(store, cfg, now_fn=lambda: now, on_error=rec.error, log=rec.log)
    return jan, rec


def poll(pred, timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


@pytest.fixture
def env(tmp_path):
    cfg = make_config(tmp_path, retention_days_success=1, retention_days_failure=2)
    store = Store(cfg.data_dir / "rcm.sqlite3")
    yield store, cfg
    store.close()


# ── sweep_once ───────────────────────────────────────────────────────────────


def test_sweep_purges_old_success_marks_it_and_keeps_a_younger_failure(env):
    store, cfg = env
    ok = finished(store, finished_at=NOW - 1.5 * DAY)  # 성공 보존 1일 → 대상
    bad = finished(store, state=FAILED, finished_at=NOW - 1.5 * DAY)  # 실패 보존 2일 → 아직
    ok_dirs = touch_dirs(cfg, ok)
    bad_dirs = touch_dirs(cfg, bad)
    jan, rec = make_janitor(store, cfg)
    assert jan.sweep_once(NOW) == 1
    assert not ok_dirs[0].exists() and not ok_dirs[1].exists()
    assert bad_dirs[0].exists() and bad_dirs[1].exists()
    assert store.get_job(ok).artifacts_purged_at == NOW
    assert store.get_job(bad).artifacts_purged_at is None
    assert rec.errors == []
    assert jan.purged_total == 1 and jan.last_sweep_at == NOW
    assert jan.sweep_once(NOW) == 0  # idempotent — 두 번째는 할 일이 없다
    assert jan.purged_total == 1
    # 실패 잡은 2일이 차면 지운다
    assert jan.sweep_once(NOW + 0.5 * DAY + timedelta(seconds=1)) == 1
    assert not bad_dirs[0].exists() and not bad_dirs[1].exists()
    assert store.get_job(bad).artifacts_purged_at is not None
    assert jan.purged_total == 2


def test_every_terminal_state_uses_the_failure_retention_except_success(env):
    store, cfg = env
    ids = {
        state: finished(store, state=state, finished_at=NOW - 1.5 * DAY)
        for state in (FAILED, TIMED_OUT, LOST)
    }
    cancelled = new_job(store, created=NOW - 1.5 * DAY)
    assert store.request_cancel(cancelled.id, "alice-laptop", NOW - 1.5 * DAY, 10) == "cancelled"
    ids["cancelled"] = cancelled.id
    for jid in ids.values():
        touch_dirs(cfg, jid)
    jan, rec = make_janitor(store, cfg)
    assert jan.sweep_once(NOW) == 0  # 1.5일 < 실패 보존 2일
    assert all(store.get_job(j).artifacts_purged_at is None for j in ids.values())
    assert jan.sweep_once(NOW + DAY) == 4  # 2.5일 ≥ 2일
    assert all(store.get_job(j).artifacts_purged_at == NOW + DAY for j in ids.values())
    assert not any((cfg.data_dir / "jobs" / str(j)).exists() for j in ids.values())
    assert rec.errors == []


def test_active_jobs_are_never_touched_even_with_zero_retention(tmp_path):
    cfg = make_config(tmp_path, retention_days_success=0, retention_days_failure=0)
    store = Store(cfg.data_dir / "rcm.sqlite3")
    try:
        running = new_job(store, created=NOW - 10 * DAY)
        assert store.claim(1, NOW - 10 * DAY).id == running.id
        cancelling = new_job(store, created=NOW - 10 * DAY)
        assert store.claim(2, NOW - 10 * DAY).id == cancelling.id
        assert store.request_cancel(cancelling.id, "alice-laptop", NOW - 9 * DAY, 10) == CANCELLING
        queued = new_job(store, created=NOW - 10 * DAY)
        uploading = new_job(store, created=NOW - 10 * DAY, state="uploading")
        dirs = {j.id: touch_dirs(cfg, j.id) for j in (running, cancelling, queued, uploading)}
        tar = cfg.data_dir / "jobs" / str(queued.id) / "tree.tar.gz"
        tar.write_bytes(b"not really a tarball")
        jan, rec = make_janitor(store, cfg)
        assert jan.sweep_once(NOW) == 0
        for jid, (job_dir, ws) in dirs.items():
            assert job_dir.exists() and ws.exists() and (ws / "sub" / "y").exists(), jid
            assert store.get_job(jid).artifacts_purged_at is None
        assert tar.exists()
        assert store.get_job(running.id).state == RUNNING
        assert rec.errors == []
        # 끝나는 순간부터는 보존 0일이라 다음 sweep 에 바로 지운다
        store.finish(running.id, SUCCEEDED, now=NOW, exit_code=0)
        assert jan.sweep_once(NOW) == 1
        assert not dirs[running.id][0].exists() and not dirs[running.id][1].exists()
        assert dirs[cancelling.id][0].exists() and dirs[queued.id][1].exists()
    finally:
        store.close()


def test_missing_directories_still_get_marked_purged_without_error(env):
    store, cfg = env
    jid = finished(store, finished_at=NOW - 3 * DAY)  # 디렉터리를 만들지 않는다
    half = finished(store, finished_at=NOW - 3 * DAY)
    touch_dirs(cfg, half)
    shutil.rmtree(cfg.data_dir / "workspaces" / str(half))  # 워크스페이스만 이미 없다
    jan, rec = make_janitor(store, cfg)
    assert jan.sweep_once(NOW) == 2
    assert store.get_job(jid).artifacts_purged_at == NOW
    assert store.get_job(half).artifacts_purged_at == NOW
    assert not (cfg.data_dir / "jobs" / str(half)).exists()
    assert rec.errors == []


def test_rmtree_failure_is_reported_without_paths_and_retried_next_sweep(env, monkeypatch):
    store, cfg = env
    jid = finished(store, finished_at=NOW - 3 * DAY)
    other = finished(store, finished_at=NOW - 3 * DAY)
    job_dir, ws = touch_dirs(cfg, jid)
    touch_dirs(cfg, other)
    real_rmtree = shutil.rmtree
    broken = {"on": True}

    def flaky_rmtree(path, *args, **kwargs):
        if broken["on"] and Path(path).name == str(jid):
            raise PermissionError(errno.EACCES, "Permission denied", str(path))
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", flaky_rmtree)
    janitor_mod = __import__("remote_ci_monitor.janitor", fromlist=["Janitor"])
    monkeypatch.setattr(janitor_mod, "rmtree", flaky_rmtree, raising=False)
    jan, rec = make_janitor(store, cfg)
    assert jan.sweep_once(NOW) == 1  # other 는 지웠고 jid 는 못 지웠다 — 잡별로 삼킨다
    assert store.get_job(jid).artifacts_purged_at is None
    assert store.get_job(other).artifacts_purged_at == NOW
    assert rec.errors, "rmtree failure must reach on_error"
    msg = rec.errors[-1]
    assert "retention" in msg and str(jid) in msg, msg
    assert "EACCES" in msg or "Permission" in msg, msg
    assert str(cfg.data_dir) not in msg and str(tmp_path_of(cfg)) not in msg, msg
    broken["on"] = False
    assert jan.sweep_once(NOW + timedelta(hours=1)) == 1  # 다음 sweep 에 다시 시도한다
    assert store.get_job(jid).artifacts_purged_at == NOW + timedelta(hours=1)
    assert not job_dir.exists() and not ws.exists()
    assert len(rec.errors) == 1


def tmp_path_of(cfg) -> Path:
    return cfg.data_dir.parent


def test_mirrors_and_unrelated_directories_survive_a_sweep(env):
    store, cfg = env
    jid = finished(store, finished_at=NOW - 3 * DAY)
    touch_dirs(cfg, jid)
    mirror = cfg.data_dir / "mirrors" / "app"
    mirror.mkdir(parents=True)
    (mirror / "HEAD").write_text("ref: refs/heads/main\n")
    stray = cfg.data_dir / "jobs" / "not-a-job-id"
    stray.mkdir(parents=True)
    db = cfg.data_dir / "rcm.sqlite3"
    jan, rec = make_janitor(store, cfg)
    assert jan.sweep_once(NOW) == 1
    assert (mirror / "HEAD").exists() and stray.exists() and db.exists()
    assert not (cfg.data_dir / "jobs" / str(jid)).exists()
    assert rec.errors == []


def test_purged_job_keeps_its_row_events_and_sample(env):
    store, cfg = env
    jid = finished(store, finished_at=NOW - 3 * DAY)
    store.add_marker(jid, "step", "build", NOW - 3 * DAY)
    touch_dirs(cfg, jid)
    jan, _ = make_janitor(store, cfg)
    assert jan.sweep_once(NOW) == 1
    j = store.get_job(jid)
    assert j is not None and j.state == SUCCEEDED and j.artifacts_purged_at == NOW
    assert [x.id for x in store.list_recent(8)] == [jid]
    assert [x.id for x in store.list_samples(NOW - 45 * DAY)] == [jid]
    assert [t.state for t in j.transitions] == [QUEUED, RUNNING, SUCCEEDED]
    assert [(m.kind, m.value) for m in store.markers(jid)] == [("step", "build")]


def test_sweep_uses_now_fn_when_now_is_omitted(env):
    store, cfg = env
    jid = finished(store, finished_at=NOW - 3 * DAY)
    touch_dirs(cfg, jid)
    jan, _ = make_janitor(store, cfg, now=NOW - 2.5 * DAY)  # now_fn 기준으로는 아직 0.5일
    assert jan.sweep_once() == 0
    assert store.get_job(jid).artifacts_purged_at is None
    later, _ = make_janitor(store, cfg, now=NOW)
    assert later.sweep_once() == 1
    assert store.get_job(jid).artifacts_purged_at == NOW


def test_start_sweeps_immediately_and_stop_joins_the_thread(env):
    store, cfg = env
    jid = finished(store, finished_at=NOW - 3 * DAY)
    job_dir, ws = touch_dirs(cfg, jid)
    jan, rec = make_janitor(store, cfg)
    before = threading.active_count()
    jan.start()
    poll(lambda: store.get_job(jid).artifacts_purged_at is not None, 2.0, "initial sweep")
    assert store.get_job(jid).artifacts_purged_at == NOW
    assert not job_dir.exists() and not ws.exists()
    t0 = time.monotonic()
    jan.stop()
    assert time.monotonic() - t0 < 2.0  # sweep 간격(기본 3600초)을 기다리지 않는다
    poll(lambda: threading.active_count() <= before, 2.0, "janitor thread to exit")
    assert rec.errors == []


# ── 서버 안에서 ───────────────────────────────────────────────────────────────


def old_success_job(srv: Server, *, days: float = 3) -> int:
    """서버 Store 에 days 일 전 끝난 alice 의 succeeded 잡 + 디렉터리."""
    jid = finished(srv.store, finished_at=datetime.now(UTC) - days * DAY)
    touch_dirs(srv.cfg, jid)
    return jid


def test_log_route_is_404_log_expired_after_purge_and_rows_remain(tmp_path):
    srv = Server(tmp_path, workers=False, retention_days_success=1)
    try:
        jid = old_success_job(srv)
        status, _, data = srv.req("GET", f"/jobs/{jid}/log", token="alice", raw=True)
        assert status == 200 and data == b"line\n"
        jan, rec = make_janitor(srv.store, srv.cfg, now=datetime.now(UTC))
        assert jan.sweep_once() == 1 and rec.errors == []
        status, body = srv.req("GET", f"/jobs/{jid}/log", token="alice")
        assert status == 404 and "log expired" in body["error"], body
        assert srv.req("GET", f"/jobs/{jid}/log", token="admin")[0] == 404
        assert srv.req("GET", f"/jobs/{jid}/log")[0] == 401  # 인증은 여전히 먼저
        assert srv.req("GET", f"/jobs/{jid}/log", token="bob")[0] == 403  # 남의 잡도 여전히 403
        # 행·전이·최근 완료는 그대로다
        status, view = srv.req("GET", f"/jobs/{jid}")
        assert status == 200 and view["state"] == "succeeded"
        assert [t["state"] for t in view["transitions"]] == ["queued", "running", "succeeded"]
        doc = srv.req("GET", "/api/status")[1]
        assert jid in [r["id"] for r in doc["pools"][0]["recent"]]
        assert doc["server"]["last_error"] is None
        # 아직 로그가 없을 뿐인 대기 잡은 404 가 아니다(`rcm logs --follow` 가 200·빈 본문에 기댄다)
        waiting = srv.submit()[1]["job_id"]
        status, headers, data = srv.req("GET", f"/jobs/{waiting}/log", token="alice", raw=True)
        assert status == 200 and data == b"" and headers["X-RCM-More"] == "1"
    finally:
        srv.close()


def test_app_start_runs_the_janitor_and_shutdown_stops_it(tmp_path):
    srv = Server(tmp_path, workers=False, retention_days_success=1)
    try:
        jid = old_success_job(srv)
        young = old_success_job(srv, days=0.5)
        srv.app.start()
        poll(lambda: srv.store.get_job(jid).artifacts_purged_at is not None, 3.0, "initial sweep")
        assert not srv.app.job_dir(jid).exists()
        assert not (srv.cfg.data_dir / "workspaces" / str(jid)).exists()
        assert srv.store.get_job(young).artifacts_purged_at is None
        assert srv.app.job_dir(young).exists()
        status, body = srv.req("GET", f"/jobs/{jid}/log", token="alice")
        assert status == 404 and "log expired" in body["error"]
        assert srv.req("GET", f"/jobs/{young}/log", token="alice")[0] == 200
        status, body = srv.req("GET", "/api/health")
        assert status == 200 and body["ok"] is True and body["janitor"] is True
        assert srv.req("GET", "/api/status")[1]["server"]["last_error"] is None
    finally:
        srv.close()
    poll(
        lambda: (
            not any("janitor" in t.name or "retention" in t.name for t in threading.enumerate())
        ),
        3.0,
        "janitor threads to stop",
    )


def test_metadata_retention_deletes_purged_rows_after_the_cutoff(tmp_path):
    """(리뷰 반영) 산출물을 지운 종료 잡은 `metadata_retention_days` 뒤 행·이벤트·합류자도 지운다"""
    cfg = make_config(
        tmp_path, retention_days_success=1, retention_days_failure=2, metadata_retention_days=3
    )
    store = Store(cfg.data_dir / "rcm.sqlite3")
    try:
        ancient = finished(store, finished_at=NOW - 10 * DAY)
        store.add_joiner(ancient, "eve-ci", "eve@ci", NOW - 10 * DAY)
        store.add_marker(ancient, "step", "build", NOW - 10 * DAY)
        recent = finished(store, finished_at=NOW - 2 * DAY)
        touch_dirs(cfg, ancient)
        touch_dirs(cfg, recent)
        jan, rec = make_janitor(store, cfg)
        assert jan.sweep_once(NOW) == 2  # 둘 다 산출물은 지웠다
        assert store.get_job(ancient) is None  # 10일 > 3일 → 행까지 지웠다
        assert store.markers(ancient) == []
        assert not (cfg.data_dir / "jobs" / str(ancient)).exists()
        kept = store.get_job(recent)
        assert kept is not None and kept.artifacts_purged_at == NOW  # 2일 < 3일 → 행은 남는다
        assert [j.id for j in store.list_recent(8)] == [recent]
        assert any("purged 2 jobs" in m for m in rec.logs), rec.logs
        assert any("deleted 1 job record" in m for m in rec.logs), rec.logs
        assert rec.errors == []
        assert jan.sweep_once(NOW) == 0 and store.get_job(recent) is not None
    finally:
        store.close()


def test_stale_janitor_shows_in_health(tmp_path):
    """(리뷰 반영) 살아 있어도 마지막 sweep 이 주기의 두 배보다 오래됐으면 503 `janitor stale`."""
    srv = Server(tmp_path, workers=False)
    try:
        srv.app.start()
        poll(lambda: srv.app.retention.last_sweep_at is not None, 3.0, "first sweep")
        assert srv.req("GET", "/api/health")[1]["janitor"] is True
        interval = srv.cfg.server.retention_sweep_interval_seconds
        srv.app.retention.last_sweep_at = datetime.now(UTC) - timedelta(seconds=3 * interval)
        status, body = srv.req("GET", "/api/health")
        assert status == 503 and body["ok"] is False and body["janitor"] is False, body
        assert "janitor stale" in body["error"]
        assert body["db"] is True and body["workers_down"] == []
    finally:
        srv.close()


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_dead_janitor_thread_shows_in_last_error_and_health(tmp_path, monkeypatch):
    def boom(self, now=None):
        raise RuntimeError("disk gone at /var/rcm/data")

    monkeypatch.setattr(Janitor, "sweep_once", boom)
    srv = Server(tmp_path, workers=False)
    try:
        assert srv.req("GET", "/api/health")[0] == 200
        srv.app.start()
        status, body = 0, None

        def dead() -> bool:
            nonlocal status, body
            status, body = srv.req("GET", "/api/health")
            return status == 503

        poll(dead, 3.0, "health to turn 503")
        assert body["ok"] is False and body["janitor"] is False, body
        assert body["error"].startswith("janitor died"), body
        assert body["db"] is True and body["workers_down"] == []  # 죽은 건 janitor 뿐이다
        doc = srv.req("GET", "/api/status")[1]
        err = doc["server"]["last_error"]
        assert err is not None and err.startswith("janitor died"), err
        assert "/var/rcm" not in err and "/var/rcm" not in json.dumps(body)  # 경로 없음
        assert doc["pools"][0]["queue"] == []  # 큐 조회 자체는 멀쩡하다 — 머리의 오류가 신호다
        assert srv.req("POST", "/jobs", token="alice", json_body={"preset": "ok"})[0] == 400
    finally:
        srv.close()


def test_log_route_tells_never_started_jobs_apart_from_expired_ones(tmp_path):
    """대기 중 취소된 잡은 로그가 있었던 적이 없다 — 「retention 이 지웠다」고 말하지 않는다."""
    srv = Server(tmp_path, workers=False)
    try:
        jid = srv.submit()[1]["job_id"]  # uploading — 프로세스가 뜬 적이 없다
        status, body = srv.req("POST", f"/jobs/{jid}/cancel", token="alice", json_body={})
        assert status == 200 and body["state"] == "cancelled"
        assert srv.store.get_job(jid).artifacts_purged_at is None
        status, body = srv.req("GET", f"/jobs/{jid}/log", token="alice")
        assert status == 404, body
        assert "log expired" not in body["error"]
        assert "before its process started" in body["error"]
    finally:
        srv.close()
