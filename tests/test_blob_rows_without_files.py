"""blob 표와 디스크가 갈라졌을 때 — «표에 있다» 를 믿지 않는다.

2026-09-13~14 사이 blob 파일이 통째로 사라졌는데 `blobs` 행 6,579개가 남아, 협상이 «있다» 고
답하고 세션은 안 올리고 자재화는 `blob_missing` 으로 죽었다(gate-fast 7회 연속 · 같은 파일). 세
자리가 각각 스스로 고친다: 협상(파일로 확인 + 유령 행 삭제) · 보존 정리(유령 행 삭제) ·
워커(blob_missing 이면 그 행 삭제).
"""

from __future__ import annotations

import hashlib

import pytest

from remote_ci_monitor.materialize import MaterializeError, blob_path, stale_blob_keys
from remote_ci_monitor.store import Store
from test_janitor import NOW, make_janitor
from test_janitor_m5 import DAY, blob_rows, put_blob
from test_worker import make_config


def h(name: str) -> str:
    return hashlib.sha256(name.encode()).hexdigest()


@pytest.fixture
def env(tmp_path):
    cfg = make_config(tmp_path, retention_days_success=1, retention_days_failure=2)
    cfg.server.snapshot_cache = True
    cfg.server.snapshot_cache_days = 30
    cfg.server.snapshot_cache_max_bytes = 4 * 1024**3
    store = Store(cfg.data_dir / "rcm.sqlite3")
    yield store, cfg
    store.close()


def test_stale_blob_keys_reports_rows_without_files_in_input_order(tmp_path):
    blobs = tmp_path / "blobs"
    present = h("present")
    p = blob_path(blobs, present)
    p.parent.mkdir(parents=True)
    p.write_bytes(b"x")
    ghost_a, ghost_b = h("ghost-a"), h("ghost-b")
    scoped = f"alice/{h('scoped')}"  # token 범위 키도 같은 규칙
    assert stale_blob_keys(blobs, [ghost_b, present, ghost_a, scoped]) == [ghost_b, ghost_a, scoped]
    assert stale_blob_keys(blobs, []) == []
    assert stale_blob_keys(blobs, [present]) == []


def test_sweep_drops_rows_whose_files_are_gone_and_keeps_the_rest(env):
    store, cfg = env
    young = put_blob(store, cfg, b"young", used_at=NOW - DAY)
    ghost = put_blob(store, cfg, b"ghost", used_at=NOW - DAY)
    blob_path(cfg.data_dir / "blobs", ghost).unlink()  # 파일만 사라졌다 — 행은 남아 있다
    jan, rec = make_janitor(store, cfg)
    jan.sweep_once(NOW)
    assert set(blob_rows(store)) == {young}, "유령 행은 보존 기간과 무관하게 지운다"
    assert blob_path(cfg.data_dir / "blobs", young).exists()
    assert any("without files" in m for m in rec.logs), rec.logs
    assert rec.errors == []
    jan.sweep_once(NOW)
    assert set(blob_rows(store)) == {young}  # idempotent


def test_materialize_error_carries_the_full_key_privately():
    e = MaterializeError("blob_missing", sha="496ebeb", key="alice/" + h("x"))
    assert e.key == "alice/" + h("x")
    assert "key" not in e.args_public, "전체 키는 공개 요약(/api/status)으로 나가면 안 된다"
    assert e.args_public == {"sha": "496ebeb"}
    assert MaterializeError("repo_missing", repo="r").key == ""
