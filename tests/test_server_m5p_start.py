"""보관함(M5p) 기동 — `App.start` 가 화해를 레인 · 청소기 · copier 보다 먼저 부르고, 재시작을 넘긴
항목을 입양하거나 되돌리고, 보관함 폴더를 잃으면 503 으로 멈춘다.

명세는 docs/m5p-workplan.md §5.3 · §15.3 · §15.5, 시나리오 docs/m5p-test-scenarios-b.md 의 `start`
줄이다. **§15.5 가 이긴다**: M5e 의 `reconcile_bundles_on_start` 는 이번에 `App.start` 에
넣지 않는다(넣으면 재시작을 넘긴 원격 잡의 uploading 묶음이 failed 가 된다). 그래서 B23 · G15 는
「부르지 않는다」를 잠근다.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

import pytest

import remote_ci_monitor.server as server_mod
from remote_ci_monitor.core import artifacts as art
from remote_ci_monitor.shelf_copier import ShelfCopier
from remote_ci_monitor.store import Store
from shelfkit import (
    PNG_1PX,
    drain_copier,
    finished_linux_job,
    item_dir,
    make_server,
    publish_payload,
    reopen,
    run_local,
    shelve_bundle,
    tar_bytes,
    wait_shelf,
    wait_until,
)


def test_b23_app_start_reconciles_the_shelf_before_lanes_janitor_and_copier(tmp_path, monkeypatch):
    calls: list[str] = []

    def spy(name: str, fn: Any) -> Any:
        def wrapped(*a: Any, **kw: Any) -> Any:
            calls.append(name)
            return fn(*a, **kw)

        return wrapped

    monkeypatch.setattr(
        server_mod, "reconcile_shelf_on_start", spy("shelf", server_mod.reconcile_shelf_on_start)
    )
    monkeypatch.setattr(server_mod, "start_workers", spy("lanes", server_mod.start_workers))
    monkeypatch.setattr(server_mod, "Janitor", spy("janitor", server_mod.Janitor))
    monkeypatch.setattr(server_mod, "ShelfCopier", spy("copier", server_mod.ShelfCopier))
    monkeypatch.setattr(
        Store, "reconcile_bundles_on_start", spy("bundles", Store.reconcile_bundles_on_start)
    )
    s = make_server(tmp_path)
    try:
        assert calls.count("shelf") == 1
        assert calls.index("shelf") < min(calls.index(n) for n in ("lanes", "janitor", "copier"))
        assert "bundles" not in calls  # §15.5 — M5e 화해는 이번에 넣지 않는다
        assert s.store.shelf_enabled is True and s.store.shelf_available is True
        assert (s.cfg.shelf_dir / ".rcm-shelf").is_file()
    finally:
        s.close()


def test_g15_start_clears_shelf_staging_and_leaves_the_rest(tmp_path, monkeypatch):
    # copier 스레드는 띄우지 않는다. `kept` 는 keep 잡이라 기동 직후 copier 가 그것을 보관하면서
    # `.staging/<잡>.<hex>/` 를 잠깐 만든다. 시험이 그 순간에 `.staging` 을 보면 화해가 못 지운
    # 것으로 읽혀 macOS CI 에서 빨갰다. 화해를 본 뒤에 시험이 copier 를 직접 한 번 돌린다
    monkeypatch.setattr(ShelfCopier, "start", lambda self: None)
    s = make_server(tmp_path, workers=False)
    kept = finished_linux_job(s)
    publish_payload(s, kept, tar_bytes([("out/a.txt", b"a")]))
    arts = s.cfg.data_dir / "artifacts"
    (arts / ".staging").mkdir(parents=True, exist_ok=True)
    (arts / ".staging" / "7.abcd.part").write_bytes(b"x")
    (arts / "123").mkdir()
    shelf = s.cfg.shelf_dir
    (shelf / ".staging" / "9.ef01" / "files").mkdir(parents=True)
    (shelf / "77").mkdir()
    s = reopen(s)
    try:
        assert list((shelf / ".staging").iterdir()) == []
        assert (shelf / "77").is_dir()  # 모르는 것은 안 지운다
        assert (arts / str(kept) / "bundle.tar").is_file()
        # M5e 화해는 돌지 않는다(§15.5) — 그 자국은 이번 마일스톤에서 남는다
        assert (arts / ".staging" / "7.abcd.part").exists() and (arts / "123").is_dir()
        # 기동 뒤의 copier 도 제 작업 폴더를 남기지 않는다
        assert s.store.shelf_item(kept).state == "pending"
        drain_copier(s)
        assert s.store.shelf_item(kept).state == "active"
        assert list((shelf / ".staging").iterdir()) == [] and (shelf / "77").is_dir()
    finally:
        s.close()


def test_e59_a_lost_shelf_folder_stops_the_shelf_with_503(tmp_path):
    s = make_server(tmp_path)
    jid = shelve_bundle(s, {"out/a.png": PNG_1PX})
    before = s.store.shelf_item(jid)
    s.close()
    moved = tmp_path / "unplugged"
    os.replace(s.cfg.shelf_dir, moved)
    s.cfg.shelf_dir.mkdir()  # 빈 마운트 지점 — 표식이 없다
    s = reopen(s, close=False)
    try:
        for path in (f"/shelf/{jid}/", f"/shelf/{jid}/f/out/a.png", f"/shelf/{jid}.zip", "/shelf/"):
            assert s.req("GET", path, raw=True)[0] == 503, path
        assert s.req("GET", f"/api/shelf/{jid}")[0] == 503
        storage = s.status()["server"]["shelf_storage"]
        assert storage["error_code"] == "shelf_dir_missing"
        assert s.store.shelf_item(jid) == before
        status, health = s.req("GET", "/api/health")
        assert status == 200 and health["shelf"]["error_code"] == "shelf_dir_missing"
        assert not (s.cfg.shelf_dir / ".rcm-shelf").exists()
        # Q12 — 그동안의 keep 잡은 곧장 failed(unavailable), 묶음을 붙잡지 않는다
        new = run_local(s, "keepgold")
        item = s.store.shelf_item(new)
        assert item.state == "failed" and item.reason_code == "unavailable"
        status, body = s.req("POST", f"/jobs/{jid}/keep", token="alice", json_body={})
        assert status == 503 and body["result"] == "disabled"
    finally:
        s.close()


def test_g16_copying_rows_are_adopted_reverted_or_failed(tmp_path, monkeypatch):
    s = make_server(tmp_path)
    adopted = shelve_bundle(s, {"out/a.png": PNG_1PX})
    manifest = item_dir(s, adopted) / "manifest.json"
    ino = manifest.stat().st_ino
    s.store.mark_shelf(adopted, "copying", s.clock.now)
    s.close()
    s = reopen(s, start=False)
    revert = finished_linux_job(s)
    publish_payload(s, revert, tar_bytes([("out/b.txt", b"b")]))
    s.store.claim_shelf_item(s.clock.now)  # 집은 채로 죽었다
    gone = finished_linux_job(s)
    publish_payload(s, gone, tar_bytes([("out/c.txt", b"c")]))
    s.store.claim_shelf_item(s.clock.now)
    (s.cfg.data_dir / "artifacts" / str(gone) / "bundle.tar").unlink()
    # (다) 묶음도 없다. `mark_bundles_purged` 는 보관 중인 묶음을 표시하지 않으므로(§15.2) 행을
    # 직접 쓴다. 그것으로 표시하면 묶음이 살아 있는 것으로 남아 화해가 (나) 처럼 되돌리고, 뒤에
    # copier 가 파일이 없는 것을 보고 접는다 — 시험이 copier 를 기다리지 않아 CI 에서 빨갰다
    s.store._conn().execute(
        "UPDATE job_artifacts SET state=?, purged_at=? WHERE job_id=?",
        (art.PURGED, s.clock.now.timestamp(), gone),
    )
    assert {s.store.shelf_item(j).state for j in (adopted, revert, gone)} == {"copying"}
    # copier 스레드는 띄우지 않는다. 화해가 남긴 상태를 본 뒤 시험이 copier 를 직접 돌린다
    monkeypatch.setattr(ShelfCopier, "start", lambda self: None)
    s.app.start()
    try:
        assert s.store.shelf_item(adopted).state == "active"
        assert manifest.stat().st_ino == ino  # 다시 풀지 않았다
        assert s.store.shelf_item(revert).state == "pending"
        item = s.store.shelf_item(gone)
        assert item.state == "failed" and item.reason_code == "bundle_gone"
        drain_copier(s)
        assert s.store.shelf_item(revert).state == "active"
        assert s.store.shelf_item(gone).state == "failed"
    finally:
        s.close()


def test_g17_a_shutdown_mid_copy_is_copied_again_after_restart(tmp_path, monkeypatch):
    s = make_server(tmp_path)
    shelf = s.cfg.shelf_dir
    entered, release = threading.Event(), threading.Event()
    first = {"blocked": False}
    real = os.replace

    def blocking(src: Any, dst: Any) -> Any:
        if Path(dst).parent == shelf and not first["blocked"]:
            first["blocked"] = True
            entered.set()
            release.wait(30)
            raise FileNotFoundError(str(src))  # 그사이 재시작이 staging 을 비웠다
        return real(src, dst)

    monkeypatch.setattr(os, "replace", blocking)
    jid = finished_linux_job(s)
    publish_payload(s, jid, tar_bytes([("out/a.txt", b"a")]))
    s.app._on_job_change(jid)
    assert entered.wait(10)
    assert any((shelf / ".staging").iterdir())
    assert s.store.shelf_item(jid).state == "copying"
    s = reopen(s)  # 옛 copier 는 os.replace 안에서 멈춰 있다 — 끄는 중에 보관 중
    try:
        wait_shelf(s, jid)
        doc = json.loads((item_dir(s, jid) / "manifest.json").read_text())
        assert [f["path"] for f in doc["files"]] == ["out/a.txt"]
        release.set()  # 옛 copier 가 이제 실패해도 행은 이미 active 다(조건부 갱신)
        wait_until(lambda: list((shelf / ".staging").iterdir()) == [], what="staging empty")
        assert s.store.shelf_item(jid).state == "active"
    finally:
        release.set()
        s.close()


@pytest.mark.parametrize("enabled", [True, False])
def test_app_sets_the_store_switch_from_the_config(tmp_path, enabled):
    s = make_server(tmp_path, workers=False, shelf_max_bytes=(1 if enabled else 0))
    try:
        assert s.store.shelf_enabled is enabled
    finally:
        s.close()
