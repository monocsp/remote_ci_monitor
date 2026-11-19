"""보관함(M5p) 원격 경로 — 진짜 `RemoteWorker` → 업로드 → finish, 전송 슬롯, 원격 경쟁 창.

명세는 docs/m5p-workplan.md §4.2 · §4.6 · §15.1 · §15.2, 시나리오 docs/m5p-test-scenarios-b.md 의
`rem` 줄이다. M5g §13 D 대로 claim · 업로드 · finish 를 손으로 만든 payload 로 흉내 내지 않는다 —
같은 프로세스의 `RemoteWorker(once=True)` 가 진짜 `WorkerClient` 로 HTTP 를 탄다. 악의적 워커(E53)만
tar 를 손으로 만들고, 올리는 길은 진짜 `WorkerClient.upload_bundle` · `finish` 다.
"""

from __future__ import annotations

import hashlib
import http.client
import io
import os
import tarfile
import threading
from pathlib import Path
from typing import Any

import pytest

from remote_ci_monitor.client import Client, WorkerClient
from remote_ci_monitor.config import WorkerConfig
from remote_ci_monitor.core import artifacts as art
from remote_ci_monitor.core.model import RUNNING, SUCCEEDED
from remote_ci_monitor.remote_worker import RemoteWorker
from remote_ci_monitor.store import Store
from shelfkit import (
    KEEPGOLD,
    MIB,
    PNG_1PX,
    fixtures,
    item_dir,
    make_server,
    manifest_of,
    shelf_presets,
    shelve_bundle,
    submit,
    upload,
    wait_job,
    wait_shelf,
    wait_until,
)
from test_server import sh

EXPANDED = "out/goldens.tar.gz.d/test/feature/home/goldens/home.png"
FIVE = {"out/a.png", "out/goldens.tar.gz", "out/manifest.json", "out/run.log", EXPANDED}


@pytest.fixture
def srv(tmp_path):
    s = make_server(tmp_path)
    s.cfg.presets = shelf_presets([sh("linplain", KEEPGOLD, artifacts=["out/*"], pool="linux")])
    yield s
    s.close()


def remote_worker(
    srv, tmp_path: Path, name: str = "lin-01"
) -> tuple[RemoteWorker, threading.Thread]:
    cfg = WorkerConfig(
        server=f"http://127.0.0.1:{srv.port}",
        token=srv.tokens[name],
        pool="linux",
        lanes=1,
        data_dir=str(tmp_path / f"w-{name}"),
    )
    w = RemoteWorker(cfg, once=True, log=lambda _m: None)
    t = threading.Thread(target=w.run, name=f"test-remote-{name}", daemon=True)
    return w, t


def run_remote(srv, tmp_path: Path, preset: str = "linkeep", **over: Any) -> int:
    status, body = submit(srv, preset, **over)
    assert status == 201, body
    jid = int(body["job_id"])
    upload(srv, jid, fixtures())
    w, t = remote_worker(srv, tmp_path)
    t.start()
    wait_job(srv, jid, timeout=60)
    w.stopping.set()
    t.join(timeout=30)
    return jid


def wrap_finish(srv, hook) -> None:
    """서버의 `Store.finish` 를 감싼다 — 원격 잡의 ready 와 finish 사이(§15.1) 창을 연다."""
    orig = srv.store.finish

    def finish(job_id: int, *args: Any, **kw: Any) -> Any:
        hook(job_id)
        return orig(job_id, *args, **kw)

    srv.store.finish = finish  # type: ignore[method-assign]


def test_b6_a_remote_keep_job_is_shelved_like_a_local_one(srv, tmp_path):
    seen: dict[int, Any] = {}

    def snap(job_id: int) -> None:
        seen[job_id] = (srv.store.shelf_item(job_id), srv.store.get_job(job_id).state)

    wrap_finish(srv, snap)
    jid = run_remote(srv, tmp_path)
    item, state = seen[jid]
    assert item is not None and item.state in ("pending", "copying", "active")
    assert state == RUNNING
    wait_shelf(srv, jid)
    assert {f["path"] for f in manifest_of(srv, jid)["files"]} == FIVE
    final = srv.store.shelf_item(jid)
    job = srv.store.get_job(jid)
    assert final.job_state == SUCCEEDED
    assert final.job_finished_at == job.finished_at.timestamp()
    assert (
        final.job_finished_at != srv.clock.now.timestamp()
    )  # 워커가 보낸 시각이지 받은 시각이 아니다


def test_b8_a_fetch_then_ack_waits_for_the_shelf(srv, tmp_path, monkeypatch):
    release = threading.Event()
    orig = Store.claim_shelf_item

    def gated(self: Store, now: Any) -> Any:
        release.wait(30)
        return orig(self, now)

    monkeypatch.setattr(Store, "claim_shelf_item", gated)
    jid = run_remote(srv, tmp_path)
    client = Client(f"http://127.0.0.1:{srv.port}", srv.tokens["alice"])
    dest = tmp_path / "fetched.tar"
    client.download_bundle(jid, dest)
    stored = srv.cfg.data_dir / "artifacts" / str(jid) / "bundle.tar"
    assert dest.read_bytes() == stored.read_bytes()
    sha = hashlib.sha256(dest.read_bytes()).hexdigest()
    out = client.ack_artifacts(jid, sha)
    assert out["purged"] is False and out["deferred"] is True
    assert stored.is_file()
    release.set()
    wait_shelf(srv, jid)
    wait_until(lambda: srv.store.get_bundle(jid)["state"] == art.PURGED, what="purged")


def stalled_zip(srv, jid: int) -> tuple[http.client.HTTPConnection, Any]:
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=30)
    conn.request("GET", f"/shelf/{jid}.zip")
    return conn, conn.getresponse()


def big_item(srv, sizes: list[int]) -> int:
    members = [(f"out/r{i}.bin", os.urandom(n)) for i, n in enumerate(sizes)]
    return shelve_bundle(srv, members, preset="lincopy")


def test_b9_shelf_zips_never_take_the_upload_slots(srv, tmp_path):
    jid = big_item(srv, [4 * MIB] * 4)
    held = [stalled_zip(srv, jid) for _ in range(4)]
    try:
        assert [r.status for _c, r in held] == [200] * 4
        status, headers, _b = srv.req("GET", f"/shelf/{jid}.zip", raw=True)
        assert status == 503 and headers["Retry-After"] == "5"
        remote = run_remote(srv, tmp_path)
        assert srv.store.get_bundle(remote)["state"] == art.READY or srv.store.shelf_item(remote)
        wait_shelf(srv, remote)
    finally:
        for c, r in held:
            r.close()
            c.close()


def test_g30_the_8_mib_line(srv):
    jid = big_item(srv, [4 * MIB] * 4)
    edge = big_item(srv, [8 * MIB, 8 * MIB + 1])
    held = [stalled_zip(srv, jid) for _ in range(4)]
    try:
        status, _h, body = srv.req("GET", f"/shelf/{edge}/f/out/r0.bin", raw=True)
        assert status == 200 and len(body) == 8 * MIB
        status, headers, _b = srv.req("GET", f"/shelf/{edge}/f/out/r1.bin", raw=True)
        assert status == 503 and headers["Retry-After"] == "5"
    finally:
        for c, r in held:
            r.close()
            c.close()


def test_e53_a_malicious_outer_bundle_cannot_plant_links_or_escape(srv, tmp_path):
    srv.registered("lin-01", pool="linux")
    status, body = submit(srv, "linkeep")
    jid = body["job_id"]
    upload(srv, jid, fixtures())
    assert srv.claimed("lin-01") == jid
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.GNU_FORMAT) as tf:
        up = tarfile.TarInfo("../up")
        up.type = tarfile.DIRTYPE
        tf.addfile(up)
        link = tarfile.TarInfo("out/x")
        link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
        tf.addfile(link)
        ok = tarfile.TarInfo("out/ok.png")
        ok.size = len(PNG_1PX)
        tf.addfile(ok, io.BytesIO(PNG_1PX))
    path = tmp_path / "evil.tar"
    path.write_bytes(buf.getvalue())
    wc = WorkerClient(f"http://127.0.0.1:{srv.port}", srv.tokens["lin-01"])
    receipt = wc.upload_bundle(jid, path)
    assert receipt["bundle_sha256"] == hashlib.sha256(buf.getvalue()).hexdigest()
    wc.finish(
        jid,
        SUCCEEDED,
        0,
        artifacts={
            "state": "ready",
            "file_count": 1,
            "total_bytes": len(PNG_1PX),
            "bundle_sha256": receipt["bundle_sha256"],
        },
    )
    doc = wait_shelf(srv, jid, ("active", "failed"))
    assert doc["state"] == "active"
    assert {f["path"] for f in doc["files"]} == {"out/ok.png"}
    assert not (item_dir(srv, jid) / "files/out/x").exists()
    assert not (srv.cfg.shelf_dir / "up").exists()
    assert not (item_dir(srv, jid) / "up").exists()
    for root, dirs, files in os.walk(srv.cfg.shelf_dir):
        for name in [*dirs, *files]:
            assert not os.path.islink(os.path.join(root, name))


def test_g12_the_copier_may_take_a_running_jobs_item_and_the_state_still_lands(srv, tmp_path):
    def hold(job_id: int) -> None:
        if srv.store.get_job(job_id).state != RUNNING:
            return
        srv.app.copier.wake()  # 다른 잡이 끝나 copier 를 깨운 것과 같다

        def copied() -> bool:
            item = srv.store.shelf_item(job_id)
            return item is not None and item.state == "active"

        wait_until(copied, what="copied while running")

    wrap_finish(srv, hold)
    jid = run_remote(srv, tmp_path)
    item = srv.store.shelf_item(jid)
    assert item.state == "active" and item.job_state == SUCCEEDED
    assert item.job_finished_at == srv.store.get_job(jid).finished_at.timestamp()


def test_g13_keep_and_a_keep_join_inside_the_window_still_shelve(srv, tmp_path):
    results: dict[str, Any] = {}

    def window(job_id: int) -> None:
        if srv.store.get_job(job_id).state != RUNNING or results:
            return
        status, body = srv.req("POST", f"/jobs/{job_id}/keep", token="alice", json_body={})
        results["keep"] = (status, body)
        job = srv.store.get_job(job_id)
        results["join"] = submit(srv, "linplain", "bob", tree_hash=job.source.tree_hash, keep=True)

    wrap_finish(srv, window)
    jid = run_remote(srv, tmp_path, preset="linplain")
    status, body = results["keep"]
    assert status == 202 and body["result"] == "requested", body
    status, joined = results["join"]
    assert status == 200 and joined["keep"] is True
    wait_shelf(srv, jid)
    assert srv.store.shelf_item(jid).job_state == SUCCEEDED


def test_rem_a_restart_between_upload_and_finish_still_shelves(srv, tmp_path):
    """G14 — 업로드를 마친 뒤 finish 직전에 서버 App 을 새로 띄운다. M5e 묶음 화해는 `App.start`
    에 없으므로(§15.5) uploading 묶음이 failed 로 바뀌지 않고, 이어 온 finish 가 ready 로 쓴다."""
    from remote_ci_monitor.server import App

    restarted: dict[str, Any] = {}

    orig_finish = WorkerClient.finish

    def finish(self: WorkerClient, job_id: int, *a: Any, **kw: Any) -> Any:
        if not restarted:
            restarted["before"] = srv.store.get_bundle(job_id)["state"]
            fresh = App(srv.cfg, srv.store, now_fn=srv.clock)
            fresh.start()
            restarted["after"] = srv.store.get_bundle(job_id)["state"]
            fresh.shutdown()
        return orig_finish(self, job_id, *a, **kw)

    WorkerClient.finish = finish  # type: ignore[method-assign]
    try:
        jid = run_remote(srv, tmp_path)
    finally:
        WorkerClient.finish = orig_finish  # type: ignore[method-assign]
    assert restarted["before"] == art.UPLOADING
    assert restarted["after"] == art.UPLOADING
    wait_shelf(srv, jid)
