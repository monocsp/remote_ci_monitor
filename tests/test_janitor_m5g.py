"""부피 회수의 실행자(M5g) — 인벤토리 · 측정 · 계획 · 삭제 · 무진전 latch.

명세 `docs/m5g-workplan.md` §4.4~§4.6. **구현보다 먼저 썼다(test-first).**

여기서 지키는 것:
- 인벤토리는 **디렉터리에서** 얻는다. `workspaces/` 와 `jobs/*/tree.tar.gz` 두 스캔은 독립이고
  job id 로 합친다 — 원격 워커 잡은 서버에 워크스페이스가 없어도 입력 tar 은 남는다.
- 실패는 실패로 올린다. `scandir` 실패는 「빈 디렉터리」가 아니고 DB 오류는 「고아」가 아니다.
- 계획은 한 회차에 한 번. 여유가 안 늘면 latch 를 세워 바닥 규칙만 멈춘다.
"""

from __future__ import annotations

import errno
from datetime import timedelta

import pytest

from remote_ci_monitor.core.model import FAILED
from remote_ci_monitor.store import Store
from test_janitor import DAY, NOW, finished, make_janitor, new_job, poll
from test_worker import make_config

GB = 1024**3


@pytest.fixture
def env(tmp_path):
    cfg = make_config(
        tmp_path,
        retention_days_success=14,
        retention_days_failure=30,
        workspace_retention_days=1,
        workspace_storage_max_bytes=0,
        min_free_bytes=0,
    )
    store = Store(cfg.data_dir / "rcm.sqlite3")
    yield store, cfg
    store.close()


def workspace(cfg, job_id: int, *, files: dict[str, int] | None = None):
    """워크스페이스 하나. `files` 는 이름 → 바이트."""
    ws = cfg.data_dir / "workspaces" / str(job_id)
    (ws / "sub").mkdir(parents=True, exist_ok=True)
    for name, size in (files or {"a": 10}).items():
        (ws / name).write_bytes(b"x" * size)
    return ws


def snapshot(cfg, job_id: int, size: int = 10):
    """`jobs/<id>/tree.tar.gz` — 입력 스냅샷. 원격 잡은 이것만 서버에 남는다."""
    job_dir = cfg.data_dir / "jobs" / str(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    tar = job_dir / "tree.tar.gz"
    tar.write_bytes(b"t" * size)
    return tar


def running(store, *, created=NOW) -> int:
    """실행 중인 잡 하나 — 큐에 넣고 claim 한다(Store 는 running 으로 바로 못 만든다)."""
    job = new_job(store, created=created)
    claimed = store.claim(1, created)
    assert claimed is not None and claimed.id == job.id
    return job.id


def by_id(plan):
    return {i.job_id: i for i in plan.items}


# ── 인벤토리: 네 모양을 union 으로 합친다 ────────────────────────────────────


def test_inventory_unions_workspace_only_tar_only_both_and_orphan(env):
    store, cfg = env
    ws_only = finished(store, state=FAILED, finished_at=NOW)
    both = finished(store, state=FAILED, finished_at=NOW)
    tar_only = finished(store, state=FAILED, finished_at=NOW)  # 원격 워커에서 돈 잡
    workspace(cfg, ws_only)
    workspace(cfg, both)
    snapshot(cfg, both)
    snapshot(cfg, tar_only)
    workspace(cfg, 9999)  # 잡 행이 없다 — 고아
    jan, _ = make_janitor(store, cfg)
    inv = {i.job_id: i for i in jan.inventory(NOW)}
    assert set(inv) == {ws_only, both, tar_only, 9999}
    assert inv[ws_only].snapshot_bytes == 0  # 없는 것은 0 이지 모르는 게 아니다
    assert inv[tar_only].workspace_bytes == 0
    assert inv[both].workspace_bytes > 0 and inv[both].snapshot_bytes > 0
    assert inv[9999].state is None and inv[9999].evictable is False


def test_a_remote_jobs_server_side_tar_is_a_volume_candidate(env):
    """워크스페이스 목록에만 기대면 이 tar 이 부피 규칙을 통째로 비껴가 로그 시계를 탄다."""
    store, cfg = env
    job = finished(store, state=FAILED, finished_at=NOW - 2 * DAY)
    tar = snapshot(cfg, job)
    jan, _ = make_janitor(store, cfg)
    assert jan.sweep_volume(NOW).items and not tar.exists()


def test_an_active_jobs_workspace_is_in_the_accounting_but_never_a_candidate(env):
    store, cfg = env
    job_id = running(store, created=NOW - 9 * DAY)
    ws = workspace(cfg, job_id, files={"big": 4096})
    jan, _ = make_janitor(store, cfg)
    plan = jan.plan(NOW)
    assert plan.items == () and plan.non_evictable_bytes > 0 and ws.exists()


# ── 실패는 실패로 올린다 ─────────────────────────────────────────────────────


def test_a_scandir_failure_is_a_measurement_failure_not_an_empty_directory(env, monkeypatch):
    store, cfg = env
    job = finished(store, state=FAILED, finished_at=NOW)
    workspace(cfg, job)
    jan, _ = make_janitor(store, cfg)

    def boom(path):
        raise OSError(errno.EACCES, "nope")

    monkeypatch.setattr(jan, "_scan_ids", boom)
    plan = jan.plan(NOW)
    assert plan.inventory_error is not None and plan.volume_bytes is None


def test_a_db_failure_is_not_an_orphan(env, monkeypatch):
    """행이 없다고 DB 가 **말했을 때만** 고아다. 오류는 회계 실패다."""
    store, cfg = env
    job = finished(store, state=FAILED, finished_at=NOW)
    workspace(cfg, job)
    jan, _ = make_janitor(store, cfg)
    monkeypatch.setattr(jan, "_jobs_by_id", lambda ids: (_ for _ in ()).throw(RuntimeError("db")))
    plan = jan.plan(NOW)
    assert plan.inventory_error is not None and plan.items == ()


def test_an_unreadable_workspace_stops_the_budget_but_not_the_age_rule(env, monkeypatch):
    store, cfg = env
    old = finished(store, state=FAILED, finished_at=NOW - 2 * DAY)
    young = finished(store, state=FAILED, finished_at=NOW)
    workspace(cfg, old)
    workspace(cfg, young)
    cfg.server.workspace_storage_max_bytes = GB
    jan, _ = make_janitor(store, cfg)
    real = jan._measure_dir
    monkeypatch.setattr(jan, "_measure_dir", lambda p: None if p.name == str(young) else real(p))
    plan = jan.plan(NOW)
    assert [i.job_id for i in plan.items] == [old]  # 나이는 크기를 안 본다
    assert plan.volume_bytes is None


def test_a_disk_usage_failure_stops_the_floor_only(env, monkeypatch):
    store, cfg = env
    job = finished(store, state=FAILED, finished_at=NOW)
    workspace(cfg, job)
    cfg.server.min_free_bytes = 10 * GB
    jan, _ = make_janitor(store, cfg)
    monkeypatch.setattr(jan, "_free_bytes", lambda: None)
    plan = jan.plan(NOW)
    assert plan.items == () and plan.projected_short_free_bytes is None
    assert plan.volume_bytes is not None  # 총량은 여전히 안다


# ── 측정 ─────────────────────────────────────────────────────────────────────


def test_size_is_blocks_on_disk_not_apparent_size(env):
    store, cfg = env
    job = finished(store, state=FAILED, finished_at=NOW)
    workspace(cfg, job, files={"a": 1})
    jan, _ = make_janitor(store, cfg)
    measured = jan._measure_dir(cfg.data_dir / "workspaces" / str(job))
    assert measured >= 512  # 1 바이트 파일도 블록 하나를 쥔다


def test_a_symlink_is_not_followed(env, tmp_path):
    store, cfg = env
    job = finished(store, state=FAILED, finished_at=NOW)
    ws = workspace(cfg, job, files={"a": 1})
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "huge").write_bytes(b"x" * 200_000)
    (ws / "link").symlink_to(outside)
    jan, _ = make_janitor(store, cfg)
    assert jan._measure_dir(ws) < 200_000


def test_a_terminal_workspace_is_measured_once_and_remembered(env):
    store, cfg = env
    job = finished(store, state=FAILED, finished_at=NOW)
    workspace(cfg, job)
    jan, _ = make_janitor(store, cfg)
    jan.inventory(NOW)
    calls = []
    real = jan._measure_dir
    jan._measure_dir = lambda p: (calls.append(p), real(p))[1]
    jan.inventory(NOW)
    assert calls == []  # mtime 이 그대로면 다시 안 잰다


def test_the_cache_is_dropped_when_the_directory_changes(env):
    store, cfg = env
    job = finished(store, state=FAILED, finished_at=NOW)
    ws = workspace(cfg, job)
    jan, _ = make_janitor(store, cfg)
    before = {i.job_id: i.workspace_bytes for i in jan.inventory(NOW)}[job]
    (ws / "more").write_bytes(b"y" * 8192)  # 최상위가 바뀌면 mtime 이 움직인다
    after = {i.job_id: i.workspace_bytes for i in jan.inventory(NOW)}[job]
    assert after > before


def test_the_cache_expires_so_inner_writes_cannot_hide_forever(env):
    """최상위 mtime 은 안쪽 파일의 append 를 못 잡는다 — 하루에 한 번은 다시 잰다."""
    store, cfg = env
    job = finished(store, state=FAILED, finished_at=NOW)
    ws = workspace(cfg, job)
    jan, _ = make_janitor(store, cfg)
    jan.inventory(NOW)
    (ws / "sub" / "grew").write_bytes(b"z" * 8192)  # 안쪽만 바뀐다
    same = {i.job_id: i.workspace_bytes for i in jan.inventory(NOW)}[job]
    later = {i.job_id: i.workspace_bytes for i in jan.inventory(NOW + timedelta(days=2))}[job]
    assert later > same


def test_an_active_workspace_is_remeasured_every_sweep(env):
    store, cfg = env
    job_id = running(store)
    ws = workspace(cfg, job_id)
    jan, _ = make_janitor(store, cfg)
    first = {i.job_id: i.workspace_bytes for i in jan.inventory(NOW)}[job_id]
    (ws / "sub" / "grew").write_bytes(b"z" * 8192)
    second = {i.job_id: i.workspace_bytes for i in jan.inventory(NOW)}[job_id]
    assert second > first


# ── 실행 ─────────────────────────────────────────────────────────────────────


def test_the_sweep_deletes_the_workspace_and_the_tar_but_keeps_the_log(env):
    """증거는 남고 부피만 간다 — 이 마일스톤의 핵심."""
    store, cfg = env
    job = finished(store, state=FAILED, finished_at=NOW - 2 * DAY)
    ws = workspace(cfg, job)
    tar = snapshot(cfg, job)
    log = cfg.data_dir / "jobs" / str(job) / "log.txt"
    log.write_text("why it broke\n")
    jan, _ = make_janitor(store, cfg)
    jan.sweep_volume(NOW)
    assert not ws.exists() and not tar.exists()
    assert log.exists() and log.read_text() == "why it broke\n"
    assert store.get_job(job).artifacts_purged_at is None  # 잡 디렉터리는 아직 산다


def test_a_job_that_became_active_between_plan_and_delete_is_skipped(env, monkeypatch):
    store, cfg = env
    job = finished(store, state=FAILED, finished_at=NOW - 2 * DAY)
    ws = workspace(cfg, job)
    jan, _ = make_janitor(store, cfg)
    plan = jan.plan(NOW)
    assert [i.job_id for i in plan.items] == [job]
    store.conn_execute = None  # 표시용 — 아래에서 상태만 갈아끼운다
    monkeypatch.setattr(jan, "_is_terminal", lambda job_id: False)
    jan.apply(plan, NOW)
    assert ws.exists()


def test_a_failed_tar_delete_is_retried_on_the_next_sweep(env, monkeypatch):
    store, cfg = env
    job = finished(store, state=FAILED, finished_at=NOW - 2 * DAY)
    ws = workspace(cfg, job)
    tar = snapshot(cfg, job)
    jan, rec = make_janitor(store, cfg)
    real = jan._remove_tree
    monkeypatch.setattr(
        jan, "_remove_tree", lambda p: real(p) if p.name != "tree.tar.gz" else _raise()
    )
    jan.sweep_volume(NOW)
    assert not ws.exists() and tar.exists() and rec.errors
    monkeypatch.setattr(jan, "_remove_tree", real)
    jan.sweep_volume(NOW)
    assert not tar.exists()


def _raise():
    raise OSError(errno.EACCES, "nope")


def test_one_plan_and_one_disk_read_per_sweep(env):
    store, cfg = env
    for _ in range(3):
        job = finished(store, state=FAILED, finished_at=NOW - 2 * DAY)
        workspace(cfg, job)
    cfg.server.min_free_bytes = GB
    jan, _ = make_janitor(store, cfg)
    plans, frees = [], []
    real_plan, real_free = jan.plan, jan._free_bytes
    jan.plan = lambda now, **kw: (plans.append(now), real_plan(now, **kw))[1]
    jan._free_bytes = lambda: (frees.append(1), real_free())[1]
    jan.sweep_volume(NOW)
    assert len(plans) == 1  # 「여유가 오를 때까지」 루프가 없다
    assert len(frees) == 2  # 계획 전에 한 번, 실행 뒤 확인에 한 번


# ── 무진전 latch ─────────────────────────────────────────────────────────────


def test_a_floor_sweep_that_frees_nothing_latches_and_stops_the_floor(env, monkeypatch):
    """지워도 df 가 안 움직이면(로컬 스냅샷·열린 파일) 계속 지우는 건 증거를 태우는 일이다."""
    store, cfg = env
    for _ in range(4):
        job = finished(store, state=FAILED, finished_at=NOW)
        workspace(cfg, job, files={"a": 4096})
    cfg.server.min_free_bytes = 100 * GB
    jan, rec = make_janitor(store, cfg)
    monkeypatch.setattr(jan, "_free_bytes", lambda: 0)  # 지워도 여유가 안 는다
    jan.sweep_volume(NOW)
    assert jan.no_progress is True
    left = list((cfg.data_dir / "workspaces").iterdir())
    jan.sweep_volume(NOW)  # 두 번째 회차는 바닥 규칙이 안 돈다
    assert list((cfg.data_dir / "workspaces").iterdir()) == left


def test_the_latch_leaves_the_age_rule_alone(env, monkeypatch):
    store, cfg = env
    old = finished(store, state=FAILED, finished_at=NOW - 2 * DAY)
    workspace(cfg, old)
    cfg.server.min_free_bytes = 100 * GB
    jan, _ = make_janitor(store, cfg)
    monkeypatch.setattr(jan, "_free_bytes", lambda: 0)
    jan.no_progress = True
    jan.sweep_volume(NOW)
    assert not (cfg.data_dir / "workspaces" / str(old)).exists()


def test_a_manual_gc_clears_the_latch(env, monkeypatch):
    store, cfg = env
    jan, _ = make_janitor(store, cfg)
    jan.no_progress = True
    monkeypatch.setattr(jan, "_free_bytes", lambda: 100 * GB)
    jan.gc(NOW, dry_run=False)
    assert jan.no_progress is False


def test_real_progress_does_not_latch(env, monkeypatch):
    store, cfg = env
    job = finished(store, state=FAILED, finished_at=NOW)
    workspace(cfg, job, files={"a": 8192})
    cfg.server.min_free_bytes = 100 * GB
    jan, _ = make_janitor(store, cfg)
    frees = iter([0, 10 * GB])
    monkeypatch.setattr(jan, "_free_bytes", lambda: next(frees, 10 * GB))
    jan.sweep_volume(NOW)
    assert jan.no_progress is False


# ── 회계 ─────────────────────────────────────────────────────────────────────


def test_storage_reports_the_last_measurement_without_touching_the_disk(env):
    store, cfg = env
    job = finished(store, state=FAILED, finished_at=NOW)
    workspace(cfg, job)
    jan, _ = make_janitor(store, cfg)
    jan.sweep_once(NOW)  # 주기 sweep — `rcm gc` 는 sweep 시각을 갱신하지 않는다
    doc = jan.storage(NOW)
    assert doc["volume_bytes"] == doc["workspace_bytes"] + doc["snapshot_bytes"]
    assert doc["measured_at"] is not None
    assert doc["last_sweep_at"] is not None and doc["next_sweep_at"] is not None


def test_a_manual_gc_does_not_pretend_the_periodic_sweep_ran(env):
    """손으로 부른 청소가 sweep 시각을 갱신하면 죽은 청소기가 살아 있는 것처럼 보인다."""
    store, cfg = env
    jan, _ = make_janitor(store, cfg)
    jan.gc(NOW, dry_run=False)
    assert jan.storage(NOW)["last_sweep_at"] is None


def test_sweep_times_are_null_before_the_first_sweep(env):
    store, cfg = env
    jan, _ = make_janitor(store, cfg)
    doc = jan.storage(NOW)
    assert doc["last_sweep_at"] is None and doc["next_sweep_at"] is None


# ── 회귀: 마지막 청소 시각이 화면에 실제로 실린다 (§3.1 A) ───────────────────


def test_artifact_storage_reports_the_real_last_sweep_time(tmp_path):
    """`getattr(self, "janitor")` 로 찾는 바람에 이 값은 **언제나 null** 이었다.

    「다음 청소가 언제인가」를 화면에 싣기로 한 마일스톤이라 여기서 잠근다.
    """
    from test_server import Server

    srv = Server(tmp_path, workers=False)
    try:
        srv.app.start()
        poll(lambda: srv.app.retention.last_sweep_at is not None, 5.0, "first sweep")
        assert srv.app.artifact_storage()["last_sweep_at"] is not None
    finally:
        srv.close()
