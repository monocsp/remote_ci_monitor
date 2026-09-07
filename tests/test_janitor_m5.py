"""보존 정리(M5a-2) — blob GC: `snapshot_cache_days` 동안 안 쓰인 blob 삭제(파일 → 행) ·
활성 잡(`uploading·queued·running·cancelling`)의 `jobs/<id>/manifest.json` 참조는 제외 ·
`snapshot_cache_max_bytes` 초과분은 `last_used_at` 오래된 순 · 종료 잡만 참조하면 대상 · idempotent.
명세는 docs/m5-workplan.md M5a-2 「저장 · 정리」. 아직 구현 전이라 빨간 것이 정상이다.

시각은 고정 NOW 기준으로 Store 에 직접 찍고 `sweep_once(now)` 로 돌린다(스레드 없음).
blob 파일 배치는 `<data_dir>/blobs/<aa>/<sha256>` 로 가정한다(명세의 `.part` 경로에서 유도).
"""

import hashlib
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from remote_ci_monitor.core.model import CANCELLING, QUEUED, RUNNING, SUCCEEDED, UPLOADING
from remote_ci_monitor.store import Store
from test_janitor import DAY, NOW, finished, make_janitor, new_job
from test_worker import make_config

GIB = 1 << 30


def attr_of(row: Any, name: str) -> Any:
    return getattr(row, name) if hasattr(row, name) else row[name]


def blob_path(cfg: Any, digest: str) -> Path:
    return cfg.data_dir / "blobs" / digest[:2] / digest


def put_blob(store: Store, cfg: Any, content: bytes, *, used_at) -> str:
    """파일 + 행. `last_used_at = used_at`."""
    digest = hashlib.sha256(content).hexdigest()
    p = blob_path(cfg, digest)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    store.record_blobs([(digest, len(content))], used_at)
    store.touch_blobs([digest], used_at)
    return digest


def write_manifest(cfg: Any, job_id: int, digests: list[str]) -> Path:
    job_dir = cfg.data_dir / "jobs" / str(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    path = job_dir / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "files": [
                    {"path": f"f{i}", "mode": 0o100644, "size": 1, "sha256": d}
                    for i, d in enumerate(digests)
                ],
                "links": [],
            }
        )
    )
    return path


def blob_rows(store: Store) -> dict[str, Any]:
    return {attr_of(r, "sha256"): r for r in store.list_blobs()}


@pytest.fixture
def env(tmp_path):
    cfg = make_config(tmp_path, retention_days_success=1, retention_days_failure=2)
    cfg.server.snapshot_cache = True
    cfg.server.snapshot_cache_days = 30
    cfg.server.snapshot_cache_max_bytes = 4 * GIB
    store = Store(cfg.data_dir / "rcm.sqlite3")
    yield store, cfg
    store.close()


# ── 기간 ─────────────────────────────────────────────────────────────────────


def test_old_unreferenced_blob_is_removed_file_and_row_young_one_kept(env):
    store, cfg = env
    old = put_blob(store, cfg, b"old", used_at=NOW - 31 * DAY)
    edge = put_blob(store, cfg, b"edge", used_at=NOW - 30 * DAY)  # 경계는 >= (보존 정리와 같다)
    young = put_blob(store, cfg, b"young", used_at=NOW - 29 * DAY)
    jan, rec = make_janitor(store, cfg)
    jan.sweep_once(NOW)
    assert not blob_path(cfg, old).exists() and not blob_path(cfg, edge).exists()
    assert blob_path(cfg, young).exists()
    assert set(blob_rows(store)) == {young}
    assert rec.errors == []
    assert any("blob" in m for m in rec.logs), rec.logs
    assert jan.sweep_once(NOW) == 0  # idempotent
    assert set(blob_rows(store)) == {young} and blob_path(cfg, young).exists()


def test_blob_referenced_by_an_active_jobs_manifest_is_kept(env):
    store, cfg = env
    uploading = new_job(store, created=NOW - 40 * DAY, state=UPLOADING)
    # claim 은 FIFO(우선순위 같으면 id 순) — running·cancelling 이 될 잡을 queued 보다 먼저 만든다
    running = new_job(store, created=NOW - 40 * DAY)
    assert store.claim(1, NOW - 40 * DAY).id == running.id
    cancelling = new_job(store, created=NOW - 40 * DAY)
    assert store.claim(2, NOW - 40 * DAY).id == cancelling.id
    queued = new_job(store, created=NOW - 40 * DAY)
    assert store.request_cancel(cancelling.id, "alice-laptop", NOW - 39 * DAY, 10) == CANCELLING
    kept = {}
    for job in (uploading, queued, running, cancelling):
        digest = put_blob(store, cfg, f"blob for {job.id}".encode(), used_at=NOW - 60 * DAY)
        write_manifest(cfg, job.id, [digest])
        kept[job.id] = digest
    loose = put_blob(store, cfg, b"nobody references me", used_at=NOW - 60 * DAY)
    jan, rec = make_janitor(store, cfg)
    jan.sweep_once(NOW)
    for job_id, digest in kept.items():
        assert blob_path(cfg, digest).exists(), job_id
        assert digest in blob_rows(store), job_id
    assert not blob_path(cfg, loose).exists() and loose not in blob_rows(store)
    assert rec.errors == []
    assert store.get_job(running.id).state == RUNNING and store.get_job(queued.id).state == QUEUED
    # running 이 끝나면 그 참조는 더는 보호가 아니다 — 다음 sweep 에 지운다
    store.finish(running.id, SUCCEEDED, now=NOW, exit_code=0)
    jan.sweep_once(NOW + timedelta(seconds=1))
    assert not blob_path(cfg, kept[running.id]).exists()
    assert kept[running.id] not in blob_rows(store)
    assert blob_path(cfg, kept[queued.id]).exists()


def test_blob_referenced_only_by_a_terminal_job_is_eligible(env):
    store, cfg = env
    done = finished(store, finished_at=NOW - 3 * DAY)
    digest = put_blob(store, cfg, b"finished job input", used_at=NOW - 31 * DAY)
    write_manifest(cfg, done, [digest])
    fresh = put_blob(store, cfg, b"fresh input", used_at=NOW - 1 * DAY)
    write_manifest(cfg, finished(store, finished_at=NOW - 3 * DAY), [fresh])
    jan, rec = make_janitor(store, cfg)
    jan.sweep_once(NOW)
    assert not blob_path(cfg, digest).exists() and digest not in blob_rows(store)
    assert blob_path(cfg, fresh).exists() and fresh in blob_rows(store)  # 아직 30일이 안 됐다
    assert rec.errors == []


def test_last_used_at_not_created_at_decides_age(env):
    store, cfg = env
    digest = put_blob(store, cfg, b"created long ago, used yesterday", used_at=NOW - 90 * DAY)
    store.touch_blobs([digest], NOW - 1 * DAY)  # manifest 가 다시 참조했다
    jan, _ = make_janitor(store, cfg)
    jan.sweep_once(NOW)
    assert blob_path(cfg, digest).exists() and digest in blob_rows(store)


# ── 크기 상한 ─────────────────────────────────────────────────────────────────


def test_max_bytes_overflow_trims_oldest_first_excluding_active_references(env):
    store, cfg = env
    cfg.server.snapshot_cache_max_bytes = 250
    oldest = put_blob(store, cfg, b"0" * 100, used_at=NOW - 5 * DAY)
    protected = put_blob(store, cfg, b"1" * 100, used_at=NOW - 4 * DAY)
    middle = put_blob(store, cfg, b"2" * 100, used_at=NOW - 3 * DAY)
    newest = put_blob(store, cfg, b"3" * 100, used_at=NOW - 1 * DAY)  # 합계 400 > 250
    active = new_job(store, created=NOW - 4 * DAY)
    write_manifest(cfg, active.id, [protected])
    jan, rec = make_janitor(store, cfg)
    jan.sweep_once(NOW)
    rows = blob_rows(store)
    assert oldest not in rows and not blob_path(cfg, oldest).exists()
    assert middle not in rows and not blob_path(cfg, middle).exists()
    assert protected in rows and blob_path(cfg, protected).exists()  # 활성 참조는 제외
    assert newest in rows and blob_path(cfg, newest).exists()
    assert sum(attr_of(r, "size") for r in rows.values()) <= 250
    assert rec.errors == []
    jan.sweep_once(NOW)  # 두 번째는 할 일이 없다
    assert set(blob_rows(store)) == {protected, newest}


def test_under_the_limit_nothing_is_trimmed(env):
    store, cfg = env
    cfg.server.snapshot_cache_max_bytes = 1000
    digests = [put_blob(store, cfg, bytes([i]) * 100, used_at=NOW - i * DAY) for i in range(5)]
    jan, _ = make_janitor(store, cfg)
    jan.sweep_once(NOW)
    assert set(blob_rows(store)) == set(digests)
    assert all(blob_path(cfg, d).exists() for d in digests)


# ── 견고함 ───────────────────────────────────────────────────────────────────


def test_missing_blob_file_still_drops_the_row_without_error(env):
    store, cfg = env
    digest = put_blob(store, cfg, b"gone", used_at=NOW - 40 * DAY)
    blob_path(cfg, digest).unlink()
    jan, rec = make_janitor(store, cfg)
    jan.sweep_once(NOW)
    assert digest not in blob_rows(store)
    assert rec.errors == []


def test_blob_gc_does_not_touch_job_artifacts_or_other_files(env):
    store, cfg = env
    digest = put_blob(store, cfg, b"old", used_at=NOW - 40 * DAY)
    active = new_job(store, created=NOW - 1 * DAY)
    manifest = write_manifest(cfg, active.id, [])
    stray = cfg.data_dir / "blobs" / "README"
    stray.write_text("not a blob\n")
    jan, rec = make_janitor(store, cfg)
    jan.sweep_once(NOW)
    assert not blob_path(cfg, digest).exists()
    assert manifest.exists() and stray.exists()
    assert (cfg.data_dir / "rcm.sqlite3").exists()
    assert rec.errors == []


def test_unreadable_manifest_protects_nothing_but_is_reported_not_fatal(env):
    store, cfg = env
    digest = put_blob(store, cfg, b"referenced by a broken manifest", used_at=NOW - 40 * DAY)
    active = new_job(store, created=NOW - 1 * DAY)
    path = write_manifest(cfg, active.id, [digest])
    path.write_text("{not json")
    jan, rec = make_janitor(store, cfg)
    jan.sweep_once(NOW)
    # 깨진 manifest 는 참조를 못 주지만 sweep 은 죽지 않고 오류로 표면화한다
    assert rec.errors, "a broken manifest must reach on_error"
    assert str(cfg.data_dir) not in rec.errors[-1]
    assert jan.dead is None
    assert blob_path(cfg, digest).exists() or digest not in blob_rows(store)  # 둘 중 하나로 일관


def test_cache_disabled_skips_blob_gc(env):
    store, cfg = env
    cfg.server.snapshot_cache = False
    digest = put_blob(store, cfg, b"left over from when the cache was on", used_at=NOW - 90 * DAY)
    jan, rec = make_janitor(store, cfg)
    jan.sweep_once(NOW)
    assert blob_path(cfg, digest).exists() and digest in blob_rows(store)
    assert rec.errors == []
