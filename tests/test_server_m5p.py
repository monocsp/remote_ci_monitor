"""보관함(M5p) 서버 — copier · ack 미루기 · 경로 · 시계 · 제출의 keep · `rcm keep` · 상태 JSON.

명세는 docs/m5p-workplan.md §4.1~§4.8 · §15(앞 절보다 이긴다), 시나리오는
docs/m5p-test-scenarios-b.md 의 `srv` 줄이다. 공통 준비는 `tests/shelfkit.py`.

- 로컬 레인 잡은 진짜 스크립트가 진짜 파일을 만들고 진짜 수집기가 묶는다(`run_local`).
- 헤더 · 시계 · 경로만 보는 시험은 리눅스 풀 keep 잡에 손으로 만든 묶음을 낸다(`shelve_bundle`).
- 시간은 `srv.clock` 으로만 민다. 보관을 기다릴 때는 `GET /api/shelf/N` 을 읽는다(시계를 안 돈다).
"""

from __future__ import annotations

import hashlib
import http.client
import io
import json
import os
import shutil
import sqlite3
import tarfile
import threading
import time
import zipfile
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from remote_ci_monitor.config import parse_preset
from remote_ci_monitor.core import artifacts as art
from remote_ci_monitor.core.model import CANCELLED, FAILED, SUCCEEDED
from remote_ci_monitor.store import Store
from shelfkit import (
    DAY,
    MIB,
    PNG_1PX,
    advance_days,
    drain_copier,
    finished_linux_job,
    fixtures,
    item_dir,
    last_used,
    make_server,
    manifest_of,
    publish_payload,
    reopen,
    run_local,
    shelf_doc,
    shelf_presets,
    shelve_bundle,
    stop_copier,
    submit,
    tar_bytes,
    targz_bytes,
    upload,
    wait_job,
    wait_shelf,
    wait_until,
)
from test_server import sh

PAGE_CSP = (
    "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; "
    "form-action 'self'; frame-ancestors 'none'"
)
FILE_CSP = "sandbox; default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'"
SHELF_STORAGE_KEYS = {
    "enabled",
    "stored_bytes",
    "limit_bytes",
    "items",
    "last_sweep_at",
    "error_code",
}


@pytest.fixture
def srv(tmp_path):
    s = make_server(tmp_path)
    yield s
    s.close()


@pytest.fixture
def idle(tmp_path):
    """copier 와 레인을 띄우지 않은 서버 — 보관 행이 `pending` 에 머문다."""
    s = make_server(tmp_path, workers=False)
    yield s
    s.close()


@pytest.fixture
def gold(srv):
    jid = run_local(srv, "keepgold")
    wait_shelf(srv, jid)
    return jid


class Gate:
    """copier 의 `finish_shelf_item` 을 `target` 잡의 호출에서만 붙잡는다. 그 항목은 `copying` 에
    선다. 시한은 없다 — 시험이 `release` 할 때까지 붙잡는다.

    `claim_shelf_item` 은 붙잡지 않는다. copier 스레드가 도는 서버에서 claim 을 감싸면, 감싸기 전에
    이미 들어간 claim 이 문을 거치지 않고 막 생긴 행을 집을 수 있다(E54 · E55 · E58 이 CI 에서
    그렇게 빨갰다). `pending` 을 보려면 `stop_copier` 로 copier 스레드를 멈추고 `drain_copier` 로
    시험이 직접 돌린다. finish 는 대상 행이 생기기 전에 문을 달면 그 경주가 없다. 어느 claim 이
    행을 집든 그 잡의 finish 는 문을 지난다.
    """

    def __init__(self, monkeypatch: Any, target: int):
        self.release = threading.Event()
        self.entered = threading.Event()
        orig = Store.finish_shelf_item
        gate = self

        def gated(store: Store, job_id: int, *args: Any, **kwargs: Any) -> Any:
            if job_id == target:
                gate.entered.set()
                gate.release.wait()
            return orig(store, job_id, *args, **kwargs)

        monkeypatch.setattr(Store, "finish_shelf_item", gated)


def page(srv, path: str, **kw: Any):
    return srv.req("GET", path, raw=True, **kw)


def files_of(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {f["path"]: f for f in manifest["files"]}


def job_view(srv, jid: int, **kw: Any) -> dict[str, Any]:
    status, body = srv.req("GET", f"/jobs/{jid}", **kw)
    assert status == 200, (status, body)
    return body


def keep(srv, jid: int, token: str = "alice"):
    return srv.req("POST", f"/jobs/{jid}/keep", token=token, json_body={})


# ══ 베스트 ════════════════════════════════════════════════════════════════════


def test_b1_a_local_keep_job_lands_on_the_shelf_with_the_tar_expanded(srv, gold):
    """B1: 원본 넷 + 펼친 PNG 하나 = 다섯 줄, 해시가 디스크와 같고 staging 은 비었다."""
    root = item_dir(srv, gold)
    files = root / "files"
    for rel in ("out/a.png", "out/goldens.tar.gz", "out/manifest.json", "out/run.log"):
        assert (files / rel).is_file(), rel
    expanded = "out/goldens.tar.gz.d/test/feature/home/goldens/home.png"
    assert (files / expanded).read_bytes() == PNG_1PX
    doc = manifest_of(srv, gold)
    assert doc["job"] == gold
    by_path = files_of(doc)
    assert set(by_path) == {
        "out/a.png",
        "out/goldens.tar.gz",
        "out/manifest.json",
        "out/run.log",
        expanded,
    }
    for rel, entry in by_path.items():
        data = (files / rel).read_bytes()
        assert entry["sha256"] == hashlib.sha256(data).hexdigest(), rel
        assert entry["size"] == len(data), rel
    assert by_path[expanded]["expanded_from"] == "out/goldens.tar.gz"
    assert all("expanded_from" not in e for p, e in by_path.items() if p != expanded)
    staging = srv.cfg.shelf_dir / ".staging"
    assert not staging.exists() or list(staging.iterdir()) == []


def test_b2_the_item_page_is_static_html_with_absolute_links_and_no_delete(srv, gold):
    status, headers, body = page(srv, f"/shelf/{gold}/")
    html = body.decode()
    assert status == 200
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert headers["Content-Security-Policy"] == PAGE_CSP
    assert headers["Referrer-Policy"] == "same-origin"
    assert "<script" not in html.lower()
    assert f'src="/shelf/{gold}/f/out/a.png"' in html
    assert f'src="/shelf/{gold}/f/out/goldens.tar.gz.d/test/feature/home/goldens/home.png"' in html
    assert f'href="/shelf/{gold}.zip"' in html
    # 파일 표에서 펼친 줄은 원본 tar 줄 바로 밑에
    table = html[html.index("<table") :]
    tar_at = table.index(f'href="/shelf/{gold}/f/out/goldens.tar.gz"')
    exp_at = table.index(f'href="/shelf/{gold}/f/out/goldens.tar.gz.d/')
    nxt = table.index(f'href="/shelf/{gold}/f/out/manifest.json"')
    assert tar_at < exp_at < nxt
    assert "delete" not in html.lower() or 'method="delete"' not in html.lower()
    assert "<form" not in html  # 보관 중 항목 페이지에는 form 이 없다(되살리기는 정리 대기만)


def test_b3_an_image_is_served_inline_inside_a_sandbox(srv, gold):
    status, headers, body = page(srv, f"/shelf/{gold}/f/out/a.png")
    assert status == 200 and body == PNG_1PX
    assert headers["Content-Type"] == "image/png"
    assert headers["Content-Length"] == str(len(PNG_1PX))
    sha = files_of(manifest_of(srv, gold))["out/a.png"]["sha256"]
    assert headers["ETag"] == f'"{sha[:16]}"'
    assert headers["Content-Disposition"].startswith("inline; ")
    assert headers["Content-Security-Policy"] == FILE_CSP
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert headers["Cache-Control"] == "private, max-age=300"


def test_b4_the_zip_is_stored_and_matches_the_manifest(srv, gold):
    status, headers, body = page(srv, f"/shelf/{gold}.zip")
    assert status == 200
    assert headers.get("Connection") == "close"
    zf = zipfile.ZipFile(io.BytesIO(body))
    infos = zf.infolist()
    assert all(i.compress_type == zipfile.ZIP_STORED for i in infos)
    by_path = files_of(manifest_of(srv, gold))
    assert {i.filename for i in infos} == set(by_path)
    for i in infos:
        assert hashlib.sha256(zf.read(i)).hexdigest() == by_path[i.filename]["sha256"]
    assert "manifest.json" not in {i.filename for i in infos}  # rcm 의 manifest 는 안 넣는다


def test_b4_a_korean_name_in_the_zip_has_the_utf8_flag(srv):
    jid = shelve_bundle(srv, {"out/결과.png": PNG_1PX})
    status, _h, body = page(srv, f"/shelf/{jid}.zip")
    assert status == 200
    info = zipfile.ZipFile(io.BytesIO(body)).infolist()[0]
    assert info.filename == "out/결과.png" and info.flag_bits & 0x800


def test_b5_if_none_match_gives_304_without_a_body(srv, gold):
    _s, headers, _b = page(srv, f"/shelf/{gold}/f/out/a.png")
    status, h2, body = page(
        srv, f"/shelf/{gold}/f/out/a.png", headers={"If-None-Match": headers["ETag"]}
    )
    assert status == 304 and body == b"" and h2["ETag"] == headers["ETag"]


def test_b7_an_ack_right_after_ready_waits_for_the_shelf(srv):
    """B7: 묶음이 ready 가 되자 소유자가 받고 ack — 보관이 끝난 뒤에야 묶음이 지워진다."""
    stop_copier(srv)  # 보관은 ack 뒤에 시험이 직접 돌린다
    jid = run_local(srv, "keepgold")
    assert srv.store.shelf_item(jid).state == "pending"
    bundle = srv.store.get_bundle(jid)
    assert bundle["state"] == art.READY
    events: list[Any] = []
    sub = srv.app.bus.subscribe()
    status, body = srv.req(
        "POST",
        f"/jobs/{jid}/artifacts/ack",
        token="alice",
        json_body={"bundle_sha256": bundle["bundle_sha256"]},
    )
    assert status == 200 and body["purged"] is False, body
    assert body["deferred"] is True and body["reason_code"] == "shelving"
    assert (srv.cfg.data_dir / "artifacts" / str(jid) / "bundle.tar").is_file()
    drain_copier(srv)
    assert srv.store.shelf_item(jid).state == "active"
    assert srv.store.get_bundle(jid)["state"] == art.PURGED
    assert not (srv.cfg.data_dir / "artifacts" / str(jid)).exists()
    status, doc = srv.req("GET", f"/jobs/{jid}/artifacts", token="alice")
    assert doc["state"] == art.PURGED
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        ev = sub.get(timeout=0.2)
        if ev is not None:
            events.append(ev)
    srv.app.bus.unsubscribe(sub)
    purged = [
        e for e in events if e.kind == "artifacts_changed" and e.data.get("state") == art.PURGED
    ]
    assert len(purged) == 1, [(e.kind, e.data) for e in events]


def test_b10_a_keep_flag_on_submit_is_frozen_and_answered_with_the_link(srv):
    status, body = submit(srv, "plaingold", keep=True)
    assert status == 201, body
    jid = body["job_id"]
    assert body["keep"] is True
    assert body["shelf_url"] == f"http://127.0.0.1:{srv.port}/shelf/{jid}/"
    assert job_view(srv, jid)["keep"] is True


def test_b10_a_plain_submit_has_keep_false_and_no_shelf_url(srv):
    status, body = submit(srv, "plaingold")
    assert status == 201 and body["keep"] is False and "shelf_url" not in body
    view = job_view(srv, body["job_id"])
    assert view["keep"] is False and view["shelf"] is None


def test_b11_a_preset_keep_needs_no_flag(srv):
    status, body = submit(srv, "keepgold")
    assert (
        status == 201
        and body["keep"] is True
        and body["shelf_url"].endswith(f"/shelf/{body['job_id']}/")
    )


def test_b12_joining_with_keep_turns_it_on_and_the_job_is_shelved(srv):
    tree = "ab" * 32
    status, first = submit(srv, "plaingold", "alice", tree_hash=tree)
    assert status == 201 and first["keep"] is False
    status, joined = submit(srv, "plaingold", "bob", tree_hash=tree, keep=True)
    assert status == 200 and joined["joined"] is True, joined
    assert joined["keep"] is True
    assert joined["shelf_url"] == f"http://127.0.0.1:{srv.port}/shelf/{first['job_id']}/"
    upload(srv, first["job_id"], fixtures())
    wait_shelf(srv, first["job_id"])


def test_g41_a_join_with_keep_false_does_not_turn_it_off(srv):
    tree = "ac" * 32
    status, first = submit(srv, "keepgold", "alice", tree_hash=tree)
    status, joined = submit(srv, "keepgold", "bob", tree_hash=tree, keep=False)
    assert status == 200 and joined["keep"] is True


def test_b13_keep_on_a_running_job_is_armed(srv):
    jid = run_local(srv, "plaingold", wait=False)
    status, body = keep(srv, jid)
    assert status in (200, 202), body
    # 잡이 빨리 끝나면 requested 일 수 있다 — 둘 다 결국 보관된다
    assert body["result"] in ("armed", "requested", "already_requested"), body
    assert body["job"] == jid and set(body["shelf"]) == {"state", "url", "reason_code"}
    wait_job(srv, jid)
    wait_shelf(srv, jid)


def test_b13_keep_on_a_queued_job_is_armed_exactly(idle):
    status, body = submit(idle, "plaingold")
    jid = body["job_id"]
    status, body = keep(idle, jid)
    assert status == 200 and body["result"] == "armed", body
    assert body["shelf"]["state"] == "waiting"
    assert job_view(idle, jid)["keep"] is True


def test_b14_keep_after_the_bundle_is_ready_is_requested_and_copied_promptly(srv):
    jid = run_local(srv, "plaingold")
    wait_until(lambda: (srv.store.get_bundle(jid) or {}).get("state") == art.READY, what="ready")
    status, body = keep(srv, jid)
    assert status == 202 and body["result"] == "requested", body
    began = time.monotonic()
    wait_shelf(srv, jid, timeout=2.5)
    assert time.monotonic() - began < 2.5


def test_b15_keep_on_a_shelved_item_restarts_its_clock(srv, gold):
    srv.clock.advance(2 * 3600)
    status, body = keep(srv, gold)
    assert status == 200 and body["result"] == "retouched", body
    assert last_used(srv, gold) == srv.clock.now.timestamp()
    advance_days(srv, 183)
    status, body = keep(srv, gold)
    assert status == 200 and body["result"] == "retouched"
    item = srv.store.shelf_item(gold)
    assert item.state == "active" and item.last_used_at == srv.clock.now.timestamp()


def test_b16_a_full_shelf_fails_and_keep_retries_after_room_is_made(tmp_path):
    s = make_server(tmp_path, shelf_max_bytes=900)
    try:
        first = shelve_bundle(s, {"out/a.txt": b"x" * 600})
        second = shelve_bundle(s, {"out/b.txt": b"y" * 600}, states=("failed",))
        assert s.store.shelf_item(second).reason_code == "shelf_full"
        status, body = s.req("DELETE", f"/shelf/{first}", token="admin")
        assert status == 200 and body == {"job_id": first, "deleted": True}, body
        status, body = keep(s, second)
        assert status == 202 and body["result"] == "retried", body
        wait_shelf(s, second)
    finally:
        s.close()


def test_b17_latest_redirects_to_the_newest_item_of_that_branch(srv):
    a = shelve_bundle(srv, {"out/a.txt": b"1"}, branch="dev")
    b = shelve_bundle(srv, {"out/a.txt": b"2"}, branch="main")
    c = shelve_bundle(srv, {"out/a.txt": b"3"}, branch="dev")
    assert a < b < c
    status, headers, _ = page(srv, "/shelf/latest/lincopy?branch=dev")
    assert status == 303 and headers["Location"] == f"/shelf/{c}/"
    status, headers, _ = page(srv, "/shelf/latest/lincopy?branch=main")
    assert status == 303 and headers["Location"] == f"/shelf/{b}/"
    status, headers, _ = page(srv, "/shelf/latest/lincopy")
    assert status == 303 and headers["Location"] == f"/shelf/{c}/"
    status, headers, _ = page(srv, "/shelf/latest/lincopy?branch=dev&lang=en")
    assert status == 303 and headers["Location"] == f"/shelf/{c}/?lang=en"


def test_e62_latest_is_404_for_an_unknown_preset_or_nothing_kept(srv):
    assert page(srv, "/shelf/latest/nosuch")[0] == 404
    assert page(srv, "/shelf/latest/keepgold?branch=dev")[0] == 404


def test_e62_latest_skips_nothing_but_shelved_states(srv):
    jid = shelve_bundle(srv, {"out/a.txt": b"1"}, branch="x")
    advance_days(srv, 183)  # 정리 대기도 「보관 중 · 정리 대기 가운데 최신」에 든다(§15.5)
    srv.app.retention.sweep_once(srv.clock.now)
    status, headers, _ = page(srv, "/shelf/latest/lincopy?branch=x")
    assert status == 303 and headers["Location"] == f"/shelf/{jid}/"


def test_b18_the_list_page_has_two_columns_a_restore_form_and_recent_removals(srv):
    a = shelve_bundle(srv, {"out/a.txt": b"1"})
    old = shelve_bundle(srv, {"out/b.txt": b"2"})
    advance_days(srv, 100)
    b = shelve_bundle(srv, {"out/d.txt": b"4"})
    gone = shelve_bundle(srv, {"out/c.txt": b"3"})
    keep(srv, a)  # a 는 다시 쓴다
    advance_days(srv, 90)  # old 는 190일, a · b · gone 은 90일
    assert srv.req("DELETE", f"/shelf/{gone}", token="admin")[0] == 200
    status, headers, body = page(srv, "/shelf/")
    html = body.decode()
    assert status == 200 and headers["Content-Security-Policy"] == PAGE_CSP
    kept_at, stale_at, removed_at = (html.index(f'id="{k}"') for k in ("kept", "stale", "removed"))
    assert kept_at < stale_at < removed_at
    kept, stale, removed = html[kept_at:stale_at], html[stale_at:removed_at], html[removed_at:]
    assert f'href="/shelf/{a}/"' in kept and f'href="/shelf/{b}/"' in kept
    assert f'href="/shelf/{old}/"' not in kept
    assert f'<form method="post" action="/shelf/{old}/restore">' in stale
    assert f"#{gone}" in removed and f'href="/shelf/{gone}/"' not in kept
    assert "/ 67.1 MB" in html  # 64 MiB 상한을 십진으로(`_bytes` 눈금)
    assert 'href="/shelf/?preset=lincopy"' in html


def test_b20_an_admin_delete_removes_the_folder_and_leaves_a_tombstone(srv, gold):
    before = srv.status()["server"]["shelf_storage"]["stored_bytes"]
    size = srv.store.shelf_item(gold).total_bytes
    status, body = srv.req("DELETE", f"/shelf/{gold}", token="admin")
    assert status == 200 and body == {"job_id": gold, "deleted": True}
    assert not item_dir(srv, gold).exists()
    item = srv.store.shelf_item(gold)
    assert item.state == "deleted" and item.total_bytes == 0
    status, _h, html = page(srv, f"/shelf/{gold}/")
    assert status == 410 and b"2026-09-06" in html
    after = srv.status()["server"]["shelf_storage"]["stored_bytes"]
    assert after == before - size
    assert srv.req("DELETE", f"/shelf/{gold}", token="admin")[0] == 410
    status, body = shelf_doc(srv, gold)
    assert status == 410 and body["state"] == "deleted" and body["deleted_at"]


def test_b21_shelf_storage_and_health_have_the_same_shape_and_no_paths(srv, tmp_path):
    shelve_bundle(srv, {"out/a.txt": b"1"})
    shelve_bundle(srv, {"out/b.txt": b"2"})
    srv.cfg.server.shelf_max_bytes = 1
    shelve_bundle(srv, {"out/c.txt": b"33"}, states=("failed",))
    status_body = json.dumps(srv.status())
    storage = srv.status()["server"]["shelf_storage"]
    assert set(storage) == SHELF_STORAGE_KEYS
    assert set(storage["items"]) == {"active", "stale", "pending", "failed"}
    assert storage["items"] == {"active": 2, "stale": 0, "pending": 0, "failed": 1}
    status, health = srv.req("GET", "/api/health")
    assert status == 200 and set(health["shelf"]) == SHELF_STORAGE_KEYS
    for text in (status_body, json.dumps(health)):
        assert str(srv.cfg.shelf_dir) not in text and str(tmp_path) not in text


def test_presets_tell_whether_they_have_artifacts_and_keep(srv):
    presets = {p["name"]: p for p in srv.status()["presets"]}
    assert presets["keepgold"]["has_artifacts"] is True and presets["keepgold"]["keep"] is True
    assert presets["plaingold"]["keep"] is False
    assert presets["noart"]["has_artifacts"] is False


# ══ 에러 ══════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("value", ["yes", 1, None])
def test_e34_keep_must_be_a_bool(srv, value):
    before = len(srv.store.list_active())
    status, body = submit(srv, "plaingold", keep=value)
    assert status == 400 and "keep" in body["error"], body
    assert len(srv.store.list_active()) == before


def test_e35_keep_without_artifacts_is_refused(srv):
    before = len(srv.store.list_active())
    status, body = submit(srv, "noart", keep=True)
    assert status == 400, body
    assert body["reason_code"] == "keep_without_artifacts"
    assert "keep_without_artifacts" in body["error"]
    assert len(srv.store.list_active()) == before


def test_e36_keep_needs_the_jobs_log_rights(srv):
    status, body = submit(srv, "plaingold", "alice", tree_hash="cd" * 32)
    jid = body["job_id"]
    submit(srv, "plaingold", "bob", tree_hash="cd" * 32)  # bob 은 합류자
    import base64

    basic = base64.b64encode(f"alice-laptop:{srv.tokens['alice']}".encode()).decode()
    assert keep(srv, jid, "carol")[0] == 403
    assert srv.req("POST", f"/jobs/{jid}/keep", json_body={})[0] == 401
    assert keep(srv, jid, "build-02")[0] == 403
    assert (
        srv.req(
            "POST", f"/jobs/{jid}/keep", json_body={}, headers={"Authorization": f"Basic {basic}"}
        )[0]
        == 401
    )
    assert keep(srv, 99999, "alice")[0] == 404
    assert srv.req("GET", f"/jobs/{jid}/keep", token="alice")[0] == 405
    assert keep(srv, jid, "bob")[0] == 200
    assert keep(srv, jid, "admin")[0] == 200


def test_e37_keep_without_a_bundle_is_409_with_the_reason(srv):
    """empty(글롭이 아무것도 안 찾음) · 글롭 없는 프리셋 · failed · skipped."""
    srv.cfg.presets = shelf_presets([sh("emptyglob", "true", artifacts=["out/*"])])
    empty = run_local(srv, "emptyglob")
    status, body = keep(srv, empty)
    assert status == 409 and body["result"] == "no_bundle" and body["reason_code"] == "empty"
    status, body = submit(srv, "noart")
    status, body = keep(srv, body["job_id"])
    assert status == 409 and body["result"] == "no_bundle" and body["reason_code"] == "no_artifacts"
    for state in (art.FAILED, art.SKIPPED, art.DROPPED):
        jid = finished_linux_job(srv)
        policy = art.ArtifactPolicy(globs=("out/*",), max_bytes=MIB, max_files=8)
        srv.store.start_collect(jid, policy, srv.clock.now)
        srv.store.set_bundle_failed(jid, state, "collect_failed", None, srv.clock.now)
        status, body = keep(srv, jid)
        assert status == 409 and body["result"] == "no_bundle", (state, body)
        assert body["reason_code"] == state


def test_e38_keep_on_a_gone_bundle_is_410(srv):
    # ack 로 지워진 묶음(합류자 0)
    jid = run_local(srv, "plaingold")
    b = srv.store.get_bundle(jid)
    srv.req(
        "POST",
        f"/jobs/{jid}/artifacts/ack",
        token="alice",
        json_body={"bundle_sha256": b["bundle_sha256"]},
    )
    status, body = keep(srv, jid)
    assert status == 410 and body["result"] == "gone", body
    # 청소 전이지만 만료가 지남
    jid2 = run_local(srv, "plaingold")
    srv.clock.advance(25 * 3600)
    status, body = keep(srv, jid2)
    assert status == 410 and body["result"] == "gone", body
    # 청소로 expired
    srv.app.retention.sweep_once(srv.clock.now)
    status, body = keep(srv, jid2)
    assert status == 410 and body["result"] == "gone", body


def test_e39_the_shelf_cap_is_judged_on_real_bytes(tmp_path):
    s = make_server(tmp_path, shelf_max_bytes=10_000)
    try:
        a = shelve_bundle(s, {"out/a.txt": b"a" * 3000})
        used = s.store.shelf_item(a).total_bytes
        big = shelve_bundle(s, {"out/b.txt": b"b" * 9000}, states=("failed",))
        item = s.store.shelf_item(big)
        assert item.reason_code == "shelf_full"
        assert s.store.get_job(big).state == SUCCEEDED
        assert s.store.get_bundle(big)["state"] == art.READY
        staging = s.cfg.shelf_dir / ".staging"
        assert not staging.exists() or list(staging.iterdir()) == []
        # 남은 만큼에 꼭 맞는 항목은 들어간다(합 = 상한)
        manifest_overhead = s.store.shelf_item(a).total_bytes - 3000
        room = 10_000 - used - manifest_overhead
        fit = shelve_bundle(s, {"out/c.txt": b"c" * room}, states=("active", "failed"))
        got = s.store.shelf_item(fit)
        if (
            got.state == "failed"
        ):  # manifest 크기가 이름 길이만큼 달라질 수 있다 — 한 바이트 덜어 본다
            pytest.fail(f"exact fit refused: {got.reason_code} used={used} room={room}")
        assert s.status()["server"]["shelf_storage"]["stored_bytes"] <= 10_000
    finally:
        s.close()


def test_e40_the_free_space_floor(tmp_path, monkeypatch):
    s = make_server(tmp_path, min_free_bytes=1024**3)
    try:
        real = shutil.disk_usage
        actual: dict[str, int] = {}

        def fake(path: Any) -> Any:
            u = real(path)
            return type(u)(u.total, u.used, actual.get("free", u.free))

        monkeypatch.setattr(shutil, "disk_usage", fake)
        probe = shelve_bundle(s, {"out/a.txt": b"a" * 100})
        size = s.store.shelf_item(probe).total_bytes
        actual["free"] = 1024**3 + size - 1
        low = shelve_bundle(s, {"out/a.txt": b"b" * 100}, states=("failed",))
        assert s.store.shelf_item(low).reason_code == "disk_low"
        actual["free"] = 1024**3 + size
        shelve_bundle(s, {"out/a.txt": b"c" * 100})
    finally:
        s.close()


def test_e41_a_disabled_shelf_refuses_new_items_but_serves_old_ones(tmp_path):
    s = make_server(tmp_path)
    old = shelve_bundle(s, {"out/a.png": PNG_1PX})
    s = reopen(s, shelf_max_bytes=0)
    try:
        jid = run_local(s, "keepgold")
        item = s.store.shelf_item(jid)
        assert item.state == "failed" and item.reason_code == "disabled"
        b = s.store.get_bundle(jid)
        status, body = s.req(
            "POST",
            f"/jobs/{jid}/artifacts/ack",
            token="alice",
            json_body={"bundle_sha256": b["bundle_sha256"]},
        )
        assert status == 200 and body["purged"] is True and body["deferred"] is False
        for path in (f"/shelf/{old}/", f"/shelf/{old}/f/out/a.png", f"/shelf/{old}.zip"):
            assert page(s, path)[0] == 200, path
        status, health = s.req("GET", "/api/health")
        assert health["shelf"]["enabled"] is False
    finally:
        s.close()


def test_e42_an_existing_destination_is_never_overwritten(srv):
    jid = finished_linux_job(srv)
    dest = item_dir(srv, jid)
    dest.mkdir(parents=True)
    (dest / "sentinel").write_text("mine")
    publish_payload(srv, jid, tar_bytes([("out/a.txt", b"a")]))
    srv.app._on_job_change(jid)
    wait_shelf(srv, jid, "failed")
    assert srv.store.shelf_item(jid).reason_code == "dest_exists"
    assert (dest / "sentinel").read_text() == "mine"
    staging = srv.cfg.shelf_dir / ".staging"
    assert not staging.exists() or list(staging.iterdir()) == []


def test_e43_an_exception_mid_copy_fails_that_item_and_the_copier_lives(srv, monkeypatch):
    import errno

    real = os.replace
    calls = {"n": 0}

    def boom(src: Any, dst: Any) -> Any:
        if Path(dst).parent == srv.cfg.shelf_dir and calls["n"] == 0:
            calls["n"] += 1
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(src, dst)

    monkeypatch.setattr(os, "replace", boom)
    jid = shelve_bundle(srv, {"out/a.txt": b"a"}, states=("failed",))
    item = srv.store.shelf_item(jid)
    assert item.reason_code == "copy_error"
    assert srv.store.get_job(jid).state == SUCCEEDED
    assert srv.store.get_bundle(jid)["state"] == art.READY
    staging = srv.cfg.shelf_dir / ".staging"
    assert not staging.exists() or list(staging.iterdir()) == []
    shelve_bundle(srv, {"out/b.txt": b"b"})


def test_e44_a_missing_bundle_file_fails_the_request(srv):
    jid = run_local(srv, "plaingold")
    (srv.cfg.data_dir / "artifacts" / str(jid) / "bundle.tar").unlink(missing_ok=True)
    status, body = keep(srv, jid)
    assert status == 202, body
    wait_shelf(srv, jid, "failed")
    assert srv.store.shelf_item(jid).reason_code == "bundle_gone"


def test_e45_a_dead_copier_shows_in_status_and_health_but_health_stays_200(srv, monkeypatch):
    class Boom(BaseException):
        pass

    def die(self: Any, now: Any) -> Any:
        raise Boom()

    # 도는 copier 의 claim 을 바꿔도 여기서는 괜찮다. 이 시험은 스레드 자신이 죽는 것을 보고
    # 보관 행을 세우지 않는다. 그래서 바꾸기 전에 들어간 claim 이 집을 행도 없다
    monkeypatch.setattr(Store, "claim_shelf_item", die)
    srv.app.copier.wake()
    wait_until(
        lambda: srv.status()["server"]["shelf_storage"]["error_code"] == "copier_dead",
        what="copier_dead",
    )
    status, health = srv.req("GET", "/api/health")
    assert status == 200 and health["ok"] is True
    assert health["shelf"]["error_code"] == "copier_dead"


def test_q15_a_repeated_exception_marks_the_copier_stuck(srv, monkeypatch):
    def fail(self: Any, now: Any) -> Any:
        raise RuntimeError("same thing")

    # E45 와 같은 까닭으로 도는 copier 의 claim 을 바꾼다 — 스레드의 셈을 보고 행은 세우지 않는다
    monkeypatch.setattr(Store, "claim_shelf_item", fail)

    def stuck() -> bool:
        srv.app.copier.wake()
        time.sleep(0.02)
        return srv.status()["server"]["shelf_storage"]["error_code"] == "copier_stuck"

    wait_until(stuck, what="copier_stuck")
    assert srv.app.copier.is_alive()


def test_q15_a_locked_database_is_retried_not_counted(srv, monkeypatch):
    orig = Store.claim_shelf_item
    calls = {"n": 0}
    locked_now = threading.Event()
    locked_now.set()

    def locked(self: Any, now: Any) -> Any:
        calls["n"] += 1
        if locked_now.is_set():
            raise sqlite3.OperationalError("database is locked")
        return orig(self, now)

    monkeypatch.setattr(Store, "claim_shelf_item", locked)
    # 행을 세우기 전에 copier 가 바꾼 claim 에 들어온 것을 본다. 바꾸기 전에 이미 들어가 있던 진짜
    # claim 은 그때 끝났고 집을 행이 없었다. 그래서 보관은 잠김을 다 지난 뒤에만 일어난다
    srv.app.copier.wake()
    wait_until(lambda: calls["n"] >= 1, what="the patched claim")
    jid = finished_linux_job(srv)
    publish_payload(srv, jid, tar_bytes([("out/a.txt", b"a")]))

    def locked_six_times() -> bool:
        srv.app.copier.wake()
        time.sleep(0.02)
        return calls["n"] >= 6

    wait_until(locked_six_times, what="six locked claims")
    # 여섯째 claim 에 들어왔으면 다섯째까지의 처리는 끝났다. 잠김을 셌다면 이미 copier_stuck 이다
    assert srv.status()["server"]["shelf_storage"]["error_code"] is None
    assert srv.store.shelf_item(jid).state == "pending"
    locked_now.clear()

    def done() -> bool:
        srv.app.copier.wake()
        time.sleep(0.02)
        return srv.store.shelf_item(jid).state == "active"

    wait_until(done, what="active after locks")
    assert srv.status()["server"]["shelf_storage"]["error_code"] is None


# ── 펼치기 (§4.4) ──


def copyfix(srv, extra: dict[str, bytes], **kw: Any) -> int:
    jid = run_local(srv, "copyfix", fixtures(extra), **kw)
    return jid


def test_e46_a_tar_bomb_is_not_expanded_and_the_reason_is_kept(srv):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo("zero.bin")
        info.size = 64 * MIB

        class Zeros(io.RawIOBase):
            def __init__(self) -> None:
                self.left = info.size

            def readable(self) -> bool:
                return True

            def readinto(self, b: Any) -> int:
                n = min(len(b), self.left)
                b[:n] = bytes(n)
                self.left -= n
                return n

        tf.addfile(info, io.BufferedReader(Zeros()))
    bomb = buf.getvalue()
    assert len(bomb) < 200 * 1024
    began = time.monotonic()
    jid = copyfix(srv, {"fixtures/bomb.tar.gz": bomb})
    doc = wait_shelf(srv, jid)
    assert time.monotonic() - began < 15
    files = item_dir(srv, jid) / "files"
    assert (files / "out/bomb.tar.gz").is_file()
    assert not (files / "out/bomb.tar.gz.d").exists()
    skipped = {e["path"]: e for e in manifest_of(srv, jid)["expand_skipped"]}
    assert skipped["out/bomb.tar.gz"]["reason"] == "too_big"
    assert doc["state"] == "active"


def test_e47_too_many_members_are_not_expanded(srv):
    many = targz_bytes({f"m{i}.txt": b"" for i in range(9)})
    jid = copyfix(srv, {"fixtures/many.tgz": many})
    wait_shelf(srv, jid)
    files = item_dir(srv, jid) / "files"
    assert (files / "out/many.tgz").is_file() and not (files / "out/many.tgz.d").exists()
    skipped = {e["path"]: e for e in manifest_of(srv, jid)["expand_skipped"]}
    assert skipped["out/many.tgz"]["reason"] == "too_many_files"


def test_e48_the_cap_is_for_the_whole_item_in_name_order(srv):
    """펼치면 600 KiB 인 tar.gz 둘(상한 1 MiB) — 이름 순서로 앞의 것만 펼친다."""
    zeros = bytes(600 * 1024)
    jid = copyfix(
        srv,
        {
            "fixtures/a.tar.gz": targz_bytes({"a.bin": zeros}),
            "fixtures/b.tar.gz": targz_bytes({"b.bin": zeros}),
        },
    )
    wait_shelf(srv, jid)
    files = item_dir(srv, jid) / "files"
    assert (files / "out/a.tar.gz.d/a.bin").is_file()
    skipped = {e["path"]: e["reason"] for e in manifest_of(srv, jid)["expand_skipped"]}
    assert skipped == {"out/b.tar.gz": "too_big"}
    assert not (files / "out/b.tar.gz.d").exists()


def test_e51_broken_tars_are_kept_as_files(srv):
    whole = targz_bytes({"x.png": PNG_1PX * 50})
    jid = copyfix(
        srv,
        {
            "fixtures/cut.tar.gz": whole[: len(whole) // 2],
            "fixtures/zip.tgz": b"PK\x03\x04" + b"\0" * 40,
            "fixtures/empty.tar": b"",
        },
    )
    doc = wait_shelf(srv, jid)
    assert doc["state"] == "active"
    skipped = {e["path"] for e in manifest_of(srv, jid)["expand_skipped"]}
    assert {"out/cut.tar.gz", "out/zip.tgz", "out/empty.tar"} <= skipped
    files = item_dir(srv, jid) / "files"
    assert not any((files / f"out/{n}.d").exists() for n in ("cut.tar.gz", "zip.tgz", "empty.tar"))


@pytest.mark.parametrize(
    "extra",
    [
        {"fixtures/g.tar.gz": targz_bytes({"Home.png": PNG_1PX, "home.png": PNG_1PX})},
        {
            "fixtures/g.tar.gz": targz_bytes({"a.png": PNG_1PX}),
            "fixtures/g.tar.gz.d/A.png": PNG_1PX,
        },
        {"fixtures/g.tar.gz": targz_bytes({"한.png": PNG_1PX, "한.png": PNG_1PX})},
    ],
    ids=["case-in-one-tar", "bundle-vs-expanded", "nfc-vs-nfd"],
)
def test_e52_name_collisions_fail_the_item(srv, extra):
    jid = copyfix(srv, extra)
    wait_shelf(srv, jid, "failed")
    assert srv.store.shelf_item(jid).reason_code == "name_collision"


def test_g35_only_one_level_and_never_zip(srv):
    inner = targz_bytes({"deep.png": PNG_1PX})
    outer = targz_bytes({"inner.tar": inner})
    jid = copyfix(srv, {"fixtures/o.tar.gz": outer, "fixtures/x.zip": b"PK\x05\x06" + bytes(18)})
    wait_shelf(srv, jid)
    files = item_dir(srv, jid) / "files"
    assert (files / "out/o.tar.gz.d/inner.tar").is_file()
    assert not (files / "out/o.tar.gz.d/inner.tar.d").exists()
    assert not (files / "out/x.zip.d").exists()


def test_g36_dot_slash_members_are_expanded(srv):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        d = tarfile.TarInfo("./")
        d.type = tarfile.DIRTYPE
        tf.addfile(d)
        d2 = tarfile.TarInfo("./test")
        d2.type = tarfile.DIRTYPE
        tf.addfile(d2)
        f = tarfile.TarInfo("./test/a.png")
        f.size = len(PNG_1PX)
        tf.addfile(f, io.BytesIO(PNG_1PX))
    jid = copyfix(srv, {"fixtures/g.tgz": buf.getvalue()})
    wait_shelf(srv, jid)
    assert (item_dir(srv, jid) / "files/out/g.tgz.d/test/a.png").read_bytes() == PNG_1PX
    assert "out/g.tgz.d/test/a.png" in files_of(manifest_of(srv, jid))


def test_g37_a_long_pax_name_is_expanded_as_is(srv):
    name = "d/" + "x" * 150 + ".png"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.PAX_FORMAT) as tf:
        info = tarfile.TarInfo(name)
        info.size = len(PNG_1PX)
        tf.addfile(info, io.BytesIO(PNG_1PX))
    jid = copyfix(srv, {"fixtures/long.tar.gz": buf.getvalue()})
    wait_shelf(srv, jid)
    assert f"out/long.tar.gz.d/{name}" in files_of(manifest_of(srv, jid))


# ── 손 삭제 · zip ──


def test_e56_delete_needs_an_admin_bearer(srv, gold):
    import base64

    basic = base64.b64encode(f"macmini-admin:{srv.tokens['admin']}".encode()).decode()
    assert srv.req("DELETE", f"/shelf/{gold}", token="alice")[0] == 403
    assert srv.req("DELETE", f"/shelf/{gold}")[0] == 401
    assert (
        srv.req("DELETE", f"/shelf/{gold}", headers={"Authorization": f"Basic {basic}"})[0] == 401
    )
    assert srv.req("DELETE", f"/shelf/{gold}", token="build-02")[0] == 403
    assert srv.req("DELETE", "/shelf/99999", token="admin")[0] == 404
    assert item_dir(srv, gold).is_dir()


def big_item(srv, mib: int = 4, count: int = 4) -> int:
    members = [(f"out/r{i}.bin", os.urandom(mib * MIB)) for i in range(count)]
    srv.cfg.server.max_artifact_bytes = 64 * MIB
    return shelve_bundle(srv, members)


def stalled(srv, path: str, method: str = "GET") -> tuple[http.client.HTTPConnection, Any]:
    conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=30)
    conn.request(method, path)
    return conn, conn.getresponse()


def test_e57_delete_during_a_zip_is_busy_then_goes_through(srv):
    jid = big_item(srv)
    conn, resp = stalled(srv, f"/shelf/{jid}.zip")
    assert resp.status == 200
    status, body = srv.req("DELETE", f"/shelf/{jid}", token="admin")
    assert status == 409 and body["reason_code"] == "shelf_item_busy", body
    assert item_dir(srv, jid).is_dir()
    # 같은 hold 중의 청소 회차는 그 항목을 건너뛴다
    advance_days(srv, 366)
    srv.app.retention.sweep_once(srv.clock.now)
    assert item_dir(srv, jid).is_dir()
    resp.read()
    conn.close()
    wait_until(
        lambda: srv.req("DELETE", f"/shelf/{jid}", token="admin")[0] in (200, 410), what="delete"
    )
    assert not item_dir(srv, jid).exists()


def test_g31_a_dropped_download_returns_the_slot_and_the_hold(srv):
    jid = big_item(srv)
    held = [stalled(srv, f"/shelf/{jid}.zip") for _ in range(4)]
    assert all(r.status == 200 for _c, r in held)
    assert page(srv, f"/shelf/{jid}.zip")[0] == 503
    for c, r in held:
        r.close()
        c.close()
    wait_until(
        lambda: srv.req("GET", f"/shelf/{jid}.zip", raw=True)[0] == 200, timeout=5, what="slot back"
    )
    wait_until(
        lambda: srv.req("DELETE", f"/shelf/{jid}", token="admin")[0] == 200,
        timeout=5,
        what="hold back",
    )


def test_g32_a_slow_reader_is_cut_off(tmp_path):
    s = make_server(tmp_path, artifact_transfer_timeout_seconds=1)
    try:
        jid = big_item(s)
        conn, resp = stalled(s, f"/shelf/{jid}.zip")
        assert resp.status == 200
        began = time.monotonic()
        wait_until(
            lambda: s.req("DELETE", f"/shelf/{jid}", token="admin")[0] == 200,
            timeout=6,
            what="hold back",
        )
        assert time.monotonic() - began < 6
        conn.close()
    finally:
        s.close()


def test_g33_a_single_file_download_holds_nothing(srv):
    jid = big_item(srv, mib=9, count=1)
    conn, resp = stalled(srv, f"/shelf/{jid}/f/out/r0.bin")
    assert resp.status == 200
    assert srv.req("DELETE", f"/shelf/{jid}", token="admin")[0] == 200
    data = resp.read()
    conn.close()
    assert len(data) == 9 * MIB


def test_g34_head_on_a_zip_holds_nothing(srv, gold):
    status, headers, body = srv.req("HEAD", f"/shelf/{gold}.zip", raw=True)
    assert status == 200 and body == b""
    assert srv.req("DELETE", f"/shelf/{gold}", token="admin")[0] == 200


def test_e58_files_are_409_while_copying(idle):
    # copier 를 띄우지 않은 서버로 본다 — 문(Gate)으로 copier 를 붙잡으면 문을 달기 전에 시작된
    # claim 이 막 생긴 행을 집어 가는 경주가 남는다(v0.7.0 릴리스 CI 에서 세 판이 그렇게 빨갰다).
    jid = finished_linux_job(idle)
    publish_payload(idle, jid, tar_bytes([("out/a.txt", b"a")]))
    assert idle.store.shelf_item(jid).state == "pending"
    status, _h, html = page(idle, f"/shelf/{jid}/")
    assert status == 200 and "보관하는 중".encode() in html
    assert page(idle, f"/shelf/{jid}/f/out/a.txt")[0] == 409
    assert page(idle, f"/shelf/{jid}.zip")[0] == 409
    # 서버가 띄우는 것과 같은 copier 를 한 번 돌리면 보관이 끝나고 파일이 열린다
    drain_copier(idle)
    assert idle.store.shelf_item(jid).state == "active"
    assert page(idle, f"/shelf/{jid}/f/out/a.txt")[0] == 200


def test_e58_files_are_409_in_the_copying_state(srv, monkeypatch):
    jid = finished_linux_job(srv)
    gate = Gate(monkeypatch, jid)
    publish_payload(srv, jid, tar_bytes([("out/a.txt", b"a")]))
    srv.app._on_job_change(jid)
    assert gate.entered.wait(10)
    assert srv.store.shelf_item(jid).state == "copying"
    assert page(srv, f"/shelf/{jid}/")[0] == 200
    assert page(srv, f"/shelf/{jid}/f/out/a.txt")[0] == 409
    assert page(srv, f"/shelf/{jid}.zip")[0] == 409
    gate.release.set()
    wait_shelf(srv, jid)


def test_e61_api_list_arguments_are_checked(srv):
    for q in ("state=bogus", "limit=0", "limit=abc", "limit=100000"):
        assert srv.req("GET", f"/api/shelf?{q}")[0] == 400, q


def test_api_list_defaults_to_kept_and_stale_and_all_has_the_rest(srv):
    a = shelve_bundle(srv, {"out/a.txt": b"1"})
    srv.cfg.server.shelf_max_bytes = 1
    f = shelve_bundle(srv, {"out/b.txt": b"22"}, states=("failed",))
    status, body = srv.req("GET", "/api/shelf")
    assert status == 200 and [i["job_id"] for i in body["items"]] == [a]
    assert set(body["storage"]) == SHELF_STORAGE_KEYS
    status, body = srv.req("GET", "/api/shelf?state=all")
    assert {i["job_id"] for i in body["items"]} == {a, f}
    status, body = srv.req("GET", "/api/shelf?state=failed&preset=lincopy")
    assert [i["job_id"] for i in body["items"]] == [f]
    item = srv.req("GET", f"/api/shelf/{a}")[1]
    assert item["url"] == f"http://127.0.0.1:{srv.port}/shelf/{a}/"
    assert {f["path"] for f in item["files"]} == {"out/a.txt"}
    assert "files" not in body["items"][0]


# ══ 엣지 — 시계 (§4.3) ═════════════════════════════════════════════════════════


def two_hours(srv) -> float:
    srv.clock.advance(2 * 3600)
    return srv.clock.now.timestamp()


def test_g1_g2_g3_the_using_routes_restart_the_clock(srv):
    jid = shelve_bundle(srv, {"out/a.png": PNG_1PX})
    for how in ("page", "file", "file304", "zip", "restore", "keep"):
        now = two_hours(srv)
        if how == "page":
            page(srv, f"/shelf/{jid}/")
        elif how == "file":
            page(srv, f"/shelf/{jid}/f/out/a.png")
        elif how == "file304":
            etag = page(srv, f"/shelf/{jid}/f/out/a.png")[1]["ETag"]
            now = two_hours(srv)
            assert page(srv, f"/shelf/{jid}/f/out/a.png", headers={"If-None-Match": etag})[0] == 304
        elif how == "zip":
            page(srv, f"/shelf/{jid}.zip")
        elif how == "restore":
            origin = f"http://127.0.0.1:{srv.port}"
            status = srv.req("POST", f"/shelf/{jid}/restore", headers={"Origin": origin})[0]
            assert status == 303
        else:
            keep(srv, jid)
        assert last_used(srv, jid) == now, how


def test_g4_head_never_restarts_the_clock(srv):
    jid = shelve_bundle(srv, {"out/a.png": PNG_1PX})
    before = last_used(srv, jid)
    two_hours(srv)
    for path in (f"/shelf/{jid}/", f"/shelf/{jid}/f/out/a.png", f"/shelf/{jid}.zip"):
        status, _h, body = srv.req("HEAD", path, raw=True)
        assert status == 200 and body == b"", path
    assert last_used(srv, jid) == before


def test_g5_g6_lists_json_and_failed_requests_do_not_restart_it(srv):
    jid = shelve_bundle(srv, {"out/a.png": PNG_1PX})
    before = last_used(srv, jid)
    two_hours(srv)
    for path in ("/shelf/", "/shelf/latest/lincopy", "/api/shelf", f"/api/shelf/{jid}"):
        assert page(srv, path)[0] in (200, 303), path
    assert page(srv, f"/shelf/{jid}/f/out/nope.png")[0] == 404
    assert page(srv, f"/shelf/{jid}/", headers={"Host": "evil.example"})[0] == 421
    assert (
        srv.req("POST", f"/shelf/{jid}/restore", headers={"Origin": "http://evil.example"})[0]
        == 403
    )
    assert last_used(srv, jid) == before


def test_g7_the_one_hour_rule_over_http(srv):
    jid = shelve_bundle(srv, {"out/a.png": PNG_1PX})
    first = two_hours(srv)
    page(srv, f"/shelf/{jid}/")
    assert last_used(srv, jid) == first
    srv.clock.advance(59 * 60)
    page(srv, f"/shelf/{jid}/")
    assert last_used(srv, jid) == first
    srv.clock.advance(2 * 60)
    page(srv, f"/shelf/{jid}/")
    assert last_used(srv, jid) == srv.clock.now.timestamp()


def test_g8_a_stale_page_shows_no_pictures_and_does_not_count_as_use(srv):
    jid = shelve_bundle(srv, {"out/a.png": PNG_1PX})
    before = last_used(srv, jid)
    advance_days(srv, 183)
    srv.app.retention.sweep_once(srv.clock.now)
    status, _h, body = page(srv, f"/shelf/{jid}/")
    html = body.decode()
    assert status == 200
    assert "<img" not in html
    assert f'<form method="post" action="/shelf/{jid}/restore">' in html
    assert f'href="/shelf/{jid}/f/out/a.png"' in html
    assert "2027-09-06 에 지워집니다" in html
    item = srv.store.shelf_item(jid)
    assert item.last_used_at == before and item.state == "stale"


def test_g9_a_file_from_a_stale_item_brings_it_back_at_once(srv):
    jid = shelve_bundle(srv, {"out/a.png": PNG_1PX})
    advance_days(srv, 183)
    srv.app.retention.sweep_once(srv.clock.now)
    assert page(srv, f"/shelf/{jid}/f/out/a.png")[0] == 200
    item = srv.store.shelf_item(jid)
    assert item.state == "active" and item.last_used_at == srv.clock.now.timestamp()


def test_g10_between_sweeps_the_stage_wins_over_the_column(srv):
    jid = shelve_bundle(srv, {"out/a.png": PNG_1PX})
    before = last_used(srv, jid)
    advance_days(srv, 183)
    assert srv.store.shelf_item(jid).state == "active"  # 청소기가 아직 안 돌았다
    html = page(srv, f"/shelf/{jid}/")[2].decode()
    assert "<img" not in html
    assert last_used(srv, jid) == before
    assert shelf_doc(srv, jid)[1]["state"] == "stale"
    assert job_view(srv, jid)["shelf"]["state"] == "stale"
    assert srv.status()["server"]["shelf_storage"]["items"]["stale"] == 1


def test_g11_past_the_delete_stage_but_not_swept_reads_as_stale(srv):
    jid = shelve_bundle(srv, {"out/a.png": PNG_1PX})
    advance_days(srv, 366)
    status, _h, body = page(srv, f"/shelf/{jid}/")
    assert status == 200 and b"<img" not in body
    assert shelf_doc(srv, jid)[1]["state"] == "stale"
    assert page(srv, f"/shelf/{jid}/f/out/a.png")[0] == 200  # 쓰면 다시 산다
    assert srv.store.shelf_item(jid).state == "active"


def test_g42_retouch_twice_in_a_minute_writes_once(srv):
    jid = shelve_bundle(srv, {"out/a.txt": b"a"})
    first = two_hours(srv)
    assert keep(srv, jid)[1]["result"] == "retouched"
    srv.clock.advance(60)
    assert keep(srv, jid)[1]["result"] == "retouched"
    # retouched 는 1시간 규칙 없이 쓴다(§15.1) — 두 번째 쓰기의 시각이 남는다
    assert last_used(srv, jid) in (first, srv.clock.now.timestamp())


def test_g43_the_manifest_is_cached_by_mtime(srv):
    jid = shelve_bundle(srv, {"out/a.txt": b"a"})
    page(srv, f"/shelf/{jid}/")
    folder = item_dir(srv, jid)
    (folder / "files/out/b.txt").write_bytes(b"b")
    doc = manifest_of(srv, jid)
    doc["files"].append(
        {
            "path": "out/b.txt",
            "size": 1,
            "sha256": hashlib.sha256(b"b").hexdigest(),
            "type": "text/plain; charset=utf-8",
        }
    )
    mpath = folder / "manifest.json"
    st = mpath.stat()
    mpath.write_text(json.dumps(doc))
    os.utime(mpath, ns=(st.st_atime_ns, st.st_mtime_ns))  # mtime 그대로 → 캐시
    assert page(srv, f"/shelf/{jid}/f/out/b.txt")[0] == 404
    os.utime(mpath, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    assert page(srv, f"/shelf/{jid}/f/out/b.txt")[0] == 200


# ── 엣지 — 깨우기 · 순서 ──


def test_g18_oldest_request_first_and_one_at_a_time(srv, monkeypatch):
    stop_copier(srv)  # 두 요청이 다 pending 이 된 뒤에 시험이 copier 를 돌린다
    order: list[int] = []
    active = {"n": 0, "max": 0}
    lock = threading.Lock()
    orig_finish = Store.finish_shelf_item

    def counting(self: Any, job_id: int, **kw: Any) -> Any:
        with lock:
            active["n"] += 1
            active["max"] = max(active["max"], active["n"])
        try:
            order.append(job_id)
            return orig_finish(self, job_id, **kw)
        finally:
            with lock:
                active["n"] -= 1

    monkeypatch.setattr(Store, "finish_shelf_item", counting)
    late = finished_linux_job(srv)
    early = finished_linux_job(srv)
    publish_payload(srv, early, tar_bytes([("out/a.txt", b"a")]))
    srv.clock.advance(1)
    publish_payload(srv, late, tar_bytes([("out/b.txt", b"b")]))
    assert {srv.store.shelf_item(j).state for j in (early, late)} == {"pending"}
    drain_copier(srv)
    assert {srv.store.shelf_item(j).state for j in (early, late)} == {"active"}
    assert order == [early, late] and early > late
    assert active["max"] == 1


#: G19 의 마감. 깨움이 없으면 copier 는 `poll_seconds` 마다 스스로 돈다. 마감이 그보다
#: 짧아야 「깨웠다」와 「다음 poll 에 주웠다」가 갈린다. 기본 poll(5초)로는 마감을 2.5초에 둘
#: 수밖에 없었고, CPU 를 나눠 쓰는 병렬 실행(3코어 macOS 러너의 pytest-xdist)에서 청소
#: 회차(1초)와 복사가 그 안에 못 끝났다. 이 시험의 copier 는 60초마다 돌게 하고 마감을 15초로
#: 둔다. 깨움 없이는 여전히 못 지난다.
G19_POLL_SECONDS = 60.0
G19_TIMEOUT = 15.0


def test_g19_the_copier_wakes_on_finish_keep_start_and_sweep(tmp_path, monkeypatch):
    from remote_ci_monitor import server as server_mod
    from remote_ci_monitor.shelf_copier import ShelfCopier

    monkeypatch.setattr(
        server_mod,
        "ShelfCopier",
        lambda *a, **kw: ShelfCopier(*a, **{**kw, "poll_seconds": G19_POLL_SECONDS}),
    )
    s = make_server(tmp_path, retention_sweep_interval_seconds=1)
    try:
        assert s.app.copier.poll_seconds == G19_POLL_SECONDS
        # 로컬 잡 종료
        jid = run_local(s, "keepgold")
        wait_shelf(s, jid, timeout=G19_TIMEOUT)
        # keep 응답 직후
        jid = run_local(s, "plaingold")
        keep(s, jid)
        wait_shelf(s, jid, timeout=G19_TIMEOUT)
        # 청소 회차(이벤트 없이 store 로 세운 pending)
        jid = finished_linux_job(s)
        publish_payload(s, jid, tar_bytes([("out/a.txt", b"a")]))
        wait_shelf(s, jid, timeout=G19_TIMEOUT)
    finally:
        s.close()
    # 기동 직후(DB 에 pending 행)
    s = make_server(tmp_path / "two", workers=False)
    jid = finished_linux_job(s)
    publish_payload(s, jid, tar_bytes([("out/z.txt", b"z")]))
    assert s.store.shelf_item(jid).state == "pending"
    s.app.start()
    try:
        assert s.app.copier.poll_seconds == G19_POLL_SECONDS
        wait_shelf(s, jid, timeout=G19_TIMEOUT)
    finally:
        s.close()


def test_g20_the_item_outlives_its_job_row(srv):
    jid = shelve_bundle(srv, {"out/a.txt": b"a"}, branch="dev")
    b = srv.store.get_bundle(jid)
    srv.req(
        "POST",
        f"/jobs/{jid}/artifacts/ack",
        token="alice",
        json_body={"bundle_sha256": b["bundle_sha256"]},
    )
    srv.store.mark_artifacts_purged([jid], srv.clock.now)
    srv.clock.advance(181 * DAY)
    page(srv, f"/shelf/{jid}/")  # 시계를 유지
    assert srv.store.delete_old_jobs(srv.clock.now - timedelta(days=180)) >= 1
    assert srv.store.get_job(jid) is None
    status, _h, body = page(srv, f"/shelf/{jid}/")
    html = body.decode()
    assert status == 200 and "lincopy" in html and "dev" in html and "alice@host" in html
    assert f'href="/#/jobs/{jid}"' not in html
    doc = shelf_doc(srv, jid)[1]
    assert (
        doc["preset"] == "lincopy" and doc["branch"] == "dev" and doc["requester"] == "alice@host"
    )


def test_g21_failure_only_keep(srv):
    ok = run_local(srv, "failkeep", inputs={"code": "0"})
    assert srv.store.get_job(ok).state == SUCCEEDED
    shelf = job_view(srv, ok)["shelf"]
    assert shelf["state"] == "none" and shelf["reason_code"] == "not_collected_on_success"
    bad = run_local(srv, "failkeep", inputs={"code": "3"})
    wait_shelf(srv, bad)
    assert srv.store.shelf_item(bad).job_state == FAILED


@pytest.mark.slow
def test_g22_a_cancelled_keep_job_is_still_shelved(srv):
    jid = run_local(srv, "slowkeep", wait=False)
    wait_until(lambda: srv.store.get_job(jid).state == "running", what="running")
    time.sleep(0.5)
    srv.req("POST", f"/jobs/{jid}/cancel", token="alice", json_body={})
    wait_job(srv, jid)
    assert srv.store.get_job(jid).state == CANCELLED
    wait_shelf(srv, jid)
    assert srv.store.shelf_item(jid).job_state == CANCELLED


def test_g23_every_public_state_is_the_same_in_status_and_job_view(srv):
    srv.cfg.server.recent_count = 50
    srv.cfg.presets = shelf_presets([sh("emptykeep", "true", keep=True, artifacts=["out/*"])])
    active = shelve_bundle(srv, {"out/a.txt": b"a"})
    stale = shelve_bundle(srv, {"out/b.txt": b"b"})
    gone = shelve_bundle(srv, {"out/c.txt": b"c"})
    srv.req("DELETE", f"/shelf/{gone}", token="admin")
    srv.cfg.server.shelf_max_bytes = 1
    full = shelve_bundle(srv, {"out/d.txt": b"dd"}, states=("failed",))
    srv.cfg.server.shelf_max_bytes = 64 * MIB
    none = run_local(srv, "emptykeep")
    plain = run_local(srv, "plaingold")
    stale_item = srv.store.shelf_item(stale)
    srv.store.mark_shelf(
        stale, "active", srv.clock.now, last_used_at=stale_item.last_used_at - 183 * DAY
    )
    stop_copier(srv)  # pending 이 그대로 머문다
    pending = finished_linux_job(srv)
    publish_payload(srv, pending, tar_bytes([("out/e.txt", b"e")]))
    waiting = submit(srv, "keepgold")[1]["job_id"]
    want = {
        active: "active",
        stale: "stale",
        gone: "deleted",
        full: "failed",
        none: "none",
        pending: "pending",
        waiting: "waiting",
    }
    rows: dict[int, dict[str, Any]] = {}
    for pool in srv.status()["pools"]:
        for row in [*(pool["queue"] or []), *(pool["recent"] or [])]:
            rows[row["id"]] = row
    for jid, state in want.items():
        assert rows[jid]["keep"] is True, jid
        assert rows[jid]["shelf"]["state"] == state, (jid, rows[jid]["shelf"])
        assert job_view(srv, jid)["shelf"]["state"] == state, jid
        assert rows[jid]["shelf"]["url"] == f"http://127.0.0.1:{srv.port}/shelf/{jid}/"
    assert rows[none]["shelf"]["reason_code"] == "empty"
    assert rows[full]["shelf"]["reason_code"] == "shelf_full"
    assert rows[plain]["keep"] is False and rows[plain]["shelf"] is None


def test_g24_the_link_prefix_follows_the_job_link_rules(srv):
    jid = submit(srv, "keepgold")[1]["job_id"]
    view = job_view(srv, jid, headers={"Host": "macmini.local:8787"})
    assert view["shelf"]["url"] == f"http://macmini.local:8787/shelf/{jid}/"
    view = job_view(srv, jid, headers={"Host": "0.0.0.0:8787"})
    assert view["shelf"]["url"].endswith(f"/shelf/{jid}/") and "0.0.0.0" not in view["shelf"]["url"]
    srv.cfg.server.public_url = "http://ci.example.com"
    status, body = submit(srv, "keepgold")
    assert body["shelf_url"] == f"http://ci.example.com/shelf/{body['job_id']}/"


def test_g38_keep_is_frozen_at_submit(srv):
    from shelfkit import SHELF_PRESETS

    status, body = submit(srv, "plaingold")
    a = body["job_id"]
    status, body = submit(srv, "keepgold")
    b = body["job_id"]
    flipped = []
    for p in SHELF_PRESETS:
        if p["name"] == "plaingold":
            p = {**p, "keep": True}
        elif p["name"] == "keepgold":
            p = {k: v for k, v in p.items() if k != "keep"}
        flipped.append(parse_preset(p))
    srv.cfg.presets = tuple(flipped)  # reload 와 같은 일 — 프리셋 표가 바뀐다
    upload(srv, a, fixtures())
    upload(srv, b, fixtures())
    wait_job(srv, a)
    wait_job(srv, b)
    assert job_view(srv, a)["keep"] is False
    assert job_view(srv, b)["keep"] is True
    wait_shelf(srv, b)
    assert srv.store.shelf_item(a) is None


def test_g39_supersede_hands_keep_to_the_new_job(idle):
    idle.cfg.presets = shelf_presets(
        [sh("supkeep", "sleep 30", artifacts=["out/*"], supersede="all")]
    )
    status, old = submit(idle, "supkeep", keep=True, branch="feat")
    upload(idle, old["job_id"], fixtures())
    status, new = submit(idle, "supkeep", branch="feat")
    assert new["keep"] is False
    upload(idle, new["job_id"], fixtures())
    assert job_view(idle, new["job_id"])["keep"] is True


def test_g40_a_git_ref_submit_carries_keep_and_the_link(idle):
    idle.cfg.presets = shelf_presets(
        [sh("gref", "true", source_modes=["git_ref"], repo="app", artifacts=["out/*"])]
    )
    body = {
        "preset": "gref",
        "inputs": {},
        "source": {"mode": "git_ref", "ref": "0123456789abcdef0123456789abcdef01234567"},
        "keep": True,
    }
    status, first = idle.req("POST", "/jobs", token="alice", json_body=body)
    assert status == 201 and first["keep"] is True, first
    assert first["shelf_url"] == f"http://127.0.0.1:{idle.port}/shelf/{first['job_id']}/"
    status, joined = idle.req("POST", "/jobs", token="bob", json_body=body)
    assert status == 200 and joined["joined"] is True and joined["keep"] is True
    assert joined["shelf_url"] == first["shelf_url"]
    status, bad = idle.req("POST", "/jobs", token="alice", json_body={**body, "keep": "on"})
    assert status == 400


def test_g45_a_shelf_change_is_an_event(srv):
    sub = srv.app.bus.subscribe()
    jid = shelve_bundle(srv, {"out/a.txt": b"a"})
    seen: list[Any] = []
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        ev = sub.get(timeout=0.2)
        if ev is not None:
            seen.append(ev)
            if ev.kind == "shelf_changed" and ev.data == {"job_id": jid, "state": "active"}:
                break
    srv.app.bus.unsubscribe(sub)
    assert any(
        e.kind == "shelf_changed" and e.data == {"job_id": jid, "state": "active"} for e in seen
    )


def test_e54_a_failed_shelf_still_lets_the_acked_bundle_go(tmp_path):
    # copier 를 띄우지 않은 서버로 본다(E58 과 같은 까닭). copier 스레드를 문(Gate)으로 붙잡으면
    # 문을 달기 전에 들어간 claim 이 막 생긴 행을 집어 ack 보다 먼저 failed 로 끝낼 수 있다. 그러면
    # ack 는 미뤄지지 않고 곧장 지운다. macOS CI 에서 `purged is False` 가 그렇게 빨갰다
    s = make_server(tmp_path, workers=False, shelf_max_bytes=10)
    try:
        jid = finished_linux_job(s)
        digest = publish_payload(s, jid, tar_bytes([("out/a.txt", b"a" * 100)]))
        assert s.store.shelf_item(jid).state == "pending"
        status, body = s.req(
            "POST", f"/jobs/{jid}/artifacts/ack", token="alice", json_body={"bundle_sha256": digest}
        )
        assert status == 200 and body["purged"] is False and body["deferred"] is True
        assert s.store.get_bundle(jid)["state"] == art.READY
        drain_copier(s)
        item = s.store.shelf_item(jid)
        assert item.state == "failed" and item.reason_code == "shelf_full"
        assert s.store.get_bundle(jid)["state"] == art.PURGED
        assert not (s.cfg.data_dir / "artifacts" / str(jid)).exists()
    finally:
        s.close()


def test_e55_the_safety_valve(tmp_path):
    s = make_server(tmp_path, retention_sweep_interval_seconds=1)
    try:
        stop_copier(s)  # copier 가 끝내 돌지 않는다 — 청소 회차만 이 행을 본다
        jid = finished_linux_job(s)
        publish_payload(s, jid, tar_bytes([("out/a.txt", b"a")]))
        s.clock.advance(25 * 3600)
        s.app.retention.sweep_once(s.clock.now)
        assert s.store.get_bundle(jid)["state"] == art.READY
        assert s.store.shelf_item(jid).state == "pending"
        s.clock.advance(24 * 3600)
        s.app.retention.sweep_once(s.clock.now)
        item = s.store.shelf_item(jid)
        assert item.state == "failed" and item.reason_code == "timeout"
        assert s.store.get_bundle(jid)["state"] == art.EXPIRED
    finally:
        s.close()


# ══ §16 추가 요구 — app-map-shots(dolomood 의 첫 사용처, 2026-11-19 리드) ═══════════════
# 다른 컴퓨터의 스크립트가 HTML 을 긁지 않고 보관 링크로 산출물을 다시 받는다.

SHOTS = {
    "build/app_map/shots.tar.gz": targz_bytes({"home.png": PNG_1PX, "home.json": b"{}"}),
    "build/app_map/manifest.json": b'{"screens":1}',
    "build/app_map/run.log": b"ok\n",
}


def test_s16_api_latest_is_the_json_twin_of_the_redirect(srv):
    older = shelve_bundle(srv, SHOTS, branch="feat/x")
    newer = shelve_bundle(srv, SHOTS, branch="feat/x")
    shelve_bundle(srv, SHOTS, branch="dev")
    before = last_used(srv, newer)
    srv.clock.advance(2 * 3600)
    status, doc = srv.req("GET", "/api/shelf/latest/lincopy?branch=feat%2Fx")
    assert status == 200 and doc["job_id"] == newer and older < newer
    status, item = srv.req("GET", f"/api/shelf/{newer}")
    assert doc == item  # 같은 모양 — 항목 + 파일 목록
    assert last_used(srv, newer) == before  # 시계를 안 돌린다
    status, body = srv.req("GET", "/api/shelf/latest/lincopy?branch=nope")
    assert status == 404 and "error" in body
    assert srv.req("GET", "/api/shelf/latest/nosuch")[0] == 404
    assert srv.req("GET", "/api/shelf/latest/lincopy", headers={"Host": "evil.example"})[0] == 421


def test_s16_api_files_carry_a_path_only_url_and_the_facts(srv):
    jid = shelve_bundle(srv, SHOTS)
    files = {f["path"]: f for f in srv.req("GET", f"/api/shelf/{jid}")[1]["files"]}
    tar = files["build/app_map/shots.tar.gz"]
    assert tar["url"] == f"/shelf/{jid}/f/build/app_map/shots.tar.gz"
    assert tar["size"] == len(SHOTS["build/app_map/shots.tar.gz"])
    assert tar["sha256"] == hashlib.sha256(SHOTS["build/app_map/shots.tar.gz"]).hexdigest()
    assert tar["type"] == "application/octet-stream" and tar["expanded_from"] is None
    png = files["build/app_map/shots.tar.gz.d/home.png"]
    assert png["expanded_from"] == "build/app_map/shots.tar.gz" and png["type"] == "image/png"
    for f in files.values():
        assert set(f) >= {"path", "url", "sha256", "size", "type", "expanded_from"}
        status, _h, body = page(srv, f["url"])  # 그 url 로 그대로 받힌다
        assert status == 200 and hashlib.sha256(body).hexdigest() == f["sha256"]


def test_s16_the_original_tar_gz_downloads_as_an_attachment_byte_for_byte(srv):
    jid = shelve_bundle(srv, SHOTS)
    status, headers, body = page(srv, f"/shelf/{jid}/f/build/app_map/shots.tar.gz")
    assert status == 200
    assert headers["Content-Type"] == "application/octet-stream"
    assert headers["Content-Disposition"].startswith('attachment; filename="shots.tar.gz"')
    assert headers["Content-Security-Policy"] == FILE_CSP
    want = SHOTS["build/app_map/shots.tar.gz"]
    assert hashlib.sha256(body).hexdigest() == hashlib.sha256(want).hexdigest()


# ══ 격리 검증 D1 — 건너뛴 멤버에도 상한이 있다 · manifest 캐시는 바이트로 ═══════════════════


def symlinks(n: int, prefix: str = "out/l") -> list[Any]:
    out = []
    for i in range(n):
        info = tarfile.TarInfo(f"{prefix}{i}")
        info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
        out.append(info)
    return out


def test_d1_a_member_bomb_in_the_outer_bundle_fails_the_item(srv):
    """바깥 묶음의 멤버(건너뛸 것까지)가 `max_artifact_files` 를 넘으면 항목이 실패한다."""
    jid = shelve_bundle(srv, [*symlinks(20), ("out/ok.txt", b"ok")], states=("failed",))
    assert srv.store.shelf_item(jid).reason_code == "too_many_files"
    assert not item_dir(srv, jid).exists()


def test_d1_a_member_bomb_inside_a_tar_is_folded_as_too_many_files(srv):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for info in symlinks(30, "l"):
            tf.addfile(info)
    jid = copyfix(srv, {"fixtures/links.tar.gz": buf.getvalue()})
    wait_shelf(srv, jid)
    doc = manifest_of(srv, jid)
    assert {e["path"]: e["reason"] for e in doc["expand_skipped"]} == {
        "out/links.tar.gz": "too_many_files"
    }
    assert len(doc["skipped"]) <= 8 and doc["skipped_count"] == len(doc["skipped"])


def test_d1_skipped_lists_are_capped_with_counts(tmp_path):
    s = make_server(tmp_path, max_artifact_files=1000)
    try:
        broken = [(f"out/b{i}.tar", b"not a tar") for i in range(250)]
        jid = shelve_bundle(s, [*symlinks(300), ("out/ok.txt", b"ok"), *broken])
        doc = manifest_of(s, jid)
        assert len(doc["skipped"]) == 200 and doc["skipped_count"] == 300
        assert len(doc["expand_skipped"]) == 200 and doc["expand_skipped_count"] == 250
        api = s.req("GET", f"/api/shelf/{jid}")[1]
        assert api["skipped_count"] == 300 and api["expand_skipped_count"] == 250
        assert len(api["skipped"]) == 200
    finally:
        s.close()


def test_d1_the_manifest_cache_is_bounded_by_bytes(srv, monkeypatch):
    import remote_ci_monitor.shelf_server as shelf_server

    a = shelve_bundle(srv, {"out/a.txt": b"a"})
    b = shelve_bundle(srv, {"out/b.txt": b"b"})
    size = (item_dir(srv, a) / "manifest.json").stat().st_size
    monkeypatch.setattr(shelf_server, "MANIFEST_CACHE_BYTES", size + size // 2)
    srv.app._shelf_manifests.clear()
    srv.app._shelf_manifest_bytes = 0
    assert srv.app.shelf_manifest(a) is not None
    assert srv.app.shelf_manifest(b) is not None
    assert set(srv.app._shelf_manifests) == {b}  # 오래된 것부터 내보낸다
    assert srv.app._shelf_manifest_bytes <= size + size // 2
    monkeypatch.setattr(shelf_server, "MANIFEST_CACHE_BYTES", size - 1)
    srv.app._shelf_manifests.clear()
    srv.app._shelf_manifest_bytes = 0
    assert srv.app.shelf_manifest(a) is not None  # 상한보다 큰 manifest 는 읽되 담지 않는다
    assert srv.app._shelf_manifests == {} and srv.app._shelf_manifest_bytes == 0


# ══ 격리 검증 개선 2 — zip 은 시작 전에 멤버를 확인하고, 도중 실패는 끊는다 ═══════════════


def test_zip_refuses_before_streaming_when_a_member_is_gone(srv):
    jid = shelve_bundle(srv, {"out/a.txt": b"a", "out/b.txt": b"b"})
    (item_dir(srv, jid) / "files/out/b.txt").unlink()
    status, headers, body = page(srv, f"/shelf/{jid}.zip")
    assert status == 409 and headers["Content-Type"].startswith("application/json")
    assert json.loads(body)["reason_code"] == "shelf_item_damaged"


def test_zip_cut_mid_stream_is_not_a_valid_zip(srv, monkeypatch):
    from remote_ci_monitor.shelf_server import ShelfHandlerMixin

    jid = shelve_bundle(srv, {"out/a.txt": b"a" * 1000, "out/b.txt": b"b" * 1000})
    orig = ShelfHandlerMixin._open_member
    calls = {"n": 0}

    def flaky(self: Any, job_id: int, rel: str) -> Any:
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("vanished")
        return orig(self, job_id, rel)

    monkeypatch.setattr(ShelfHandlerMixin, "_open_member", flaky)
    try:
        status, _h, body = page(srv, f"/shelf/{jid}.zip")
    except (http.client.IncompleteRead, ConnectionError):
        return  # 받는 쪽이 실패를 안다
    assert status == 200
    with pytest.raises(zipfile.BadZipFile):
        zipfile.ZipFile(io.BytesIO(body)).testzip()
