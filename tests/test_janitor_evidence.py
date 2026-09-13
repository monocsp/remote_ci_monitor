"""증거 불변 — 어떤 압박에서도 증거는 안 지운다(M5g §4.1의 안전 성질).

부피(워크스페이스 720 MB)를 회수하려고 증거(로그 50 KB)를 건드리면 이 마일스톤은 실패한 것이다.
예산과 바닥을 **가장 세게** 걸어 놓고, 그때 무엇이 살아남아야 하는지를 한 자리에서 잠근다.

각자의 예산이 있는 것(blob · 번들)과 아예 안 지우는 것(미러)도 여기 함께 둔다 — 부피 규칙이
남의 회계를 침범하지 않는다는 것이 같은 성질이다.
"""

from __future__ import annotations

import json

import pytest

from remote_ci_monitor.core.model import FAILED
from remote_ci_monitor.store import Store
from test_janitor import DAY, NOW, finished, make_janitor
from test_worker import make_config


@pytest.fixture
def env(tmp_path):
    cfg = make_config(
        tmp_path,
        retention_days_success=14,
        retention_days_failure=30,
        metadata_retention_days=180,
        workspace_retention_days=0,  # 부피는 끝나자마자
        workspace_storage_max_bytes=1024**3,  # 그리고 예산은 최소
        min_free_bytes=1024**5,  # 그리고 바닥은 도달 불가능하게
        snapshot_cache=True,
    )
    store = Store(cfg.data_dir / "rcm.sqlite3")
    yield store, cfg
    store.close()


def full_job(store, cfg, *, age_days: float = 0.0) -> int:
    """로그·manifest·워크스페이스·스냅샷 tar 을 다 갖춘 실패 잡 하나."""
    job_id = finished(store, state=FAILED, finished_at=NOW - age_days * DAY)
    job_dir = cfg.data_dir / "jobs" / str(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "log.txt").write_text("Expected: 3\nActual: 4\n")
    (job_dir / "manifest.json").write_text(json.dumps({"files": []}))
    (job_dir / "tree.tar.gz").write_bytes(b"t" * 4096)
    ws = cfg.data_dir / "workspaces" / str(job_id)
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "big").write_bytes(b"x" * 65536)
    return job_id


def test_pressure_takes_the_volume_and_leaves_every_piece_of_evidence(env):
    store, cfg = env
    job_id = full_job(store, cfg)
    job_dir = cfg.data_dir / "jobs" / str(job_id)
    jan, _ = make_janitor(store, cfg)
    jan.sweep_once(NOW)

    # 간 것 — 부피
    assert not (cfg.data_dir / "workspaces" / str(job_id)).exists()
    assert not (job_dir / "tree.tar.gz").exists()

    # 남은 것 — 증거
    assert (job_dir / "log.txt").read_text() == "Expected: 3\nActual: 4\n"
    assert (job_dir / "manifest.json").exists()
    job = store.get_job(job_id)
    assert job is not None and job.state == FAILED
    assert job.artifacts_purged_at is None  # 잡 디렉터리는 아직 자기 시계 안이다
    assert job_id in store.markers_for([job_id])  # 이벤트도 산다


def test_pressure_does_not_touch_mirrors(env):
    store, cfg = env
    full_job(store, cfg)
    mirror = cfg.data_dir / "mirrors" / "app"
    (mirror / "objects").mkdir(parents=True)
    (mirror / "objects" / "pack").write_bytes(b"m" * 65536)
    jan, _ = make_janitor(store, cfg)
    jan.sweep_once(NOW)
    assert (mirror / "objects" / "pack").exists()


def test_pressure_does_not_touch_artifact_bundles(env):
    """번들은 자기 시계(24시간)와 자기 예산이 있다 — 결정 41(남의 것을 쫓아내지 않는다)."""
    store, cfg = env
    job_id = full_job(store, cfg)
    bundle = cfg.data_dir / "artifacts" / str(job_id)
    bundle.mkdir(parents=True)
    (bundle / "bundle.tar").write_bytes(b"b" * 65536)
    jan, _ = make_janitor(store, cfg)
    jan.sweep_once(NOW)
    assert (bundle / "bundle.tar").exists()


def test_pressure_does_not_touch_the_snapshot_blob_cache(env):
    """blob 은 `snapshot_cache_max_bytes`(4 GiB)가 지배한다 — 부피 예산이 손대지 않는다."""
    store, cfg = env
    full_job(store, cfg)
    blob = cfg.data_dir / "blobs" / "aa"
    blob.mkdir(parents=True)
    (blob / "bb").write_bytes(b"c" * 65536)
    jan, _ = make_janitor(store, cfg)
    jan.sweep_once(NOW)
    assert (blob / "bb").exists()


def test_an_unrelated_directory_in_the_data_dir_is_never_touched(env):
    store, cfg = env
    full_job(store, cfg)
    stray = cfg.data_dir / "backup"
    stray.mkdir(parents=True)
    (stray / "rcm.sqlite3.bak").write_bytes(b"d" * 4096)
    jan, _ = make_janitor(store, cfg)
    jan.sweep_once(NOW)
    assert (stray / "rcm.sqlite3.bak").exists()
