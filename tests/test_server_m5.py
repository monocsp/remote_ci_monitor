"""서버(M5a) — 우선순위 제출·변경 라우트 · 내용 주소 스냅샷 캐시(manifest → missing → blob PUT) ·
캐시 범위(token) · 상태 JSON 의 `snapshot_cache`/`notify_failures` · blob 자재화.
명세는 docs/m5-workplan.md M5a-1 · M5a-2. 아직 구현 전이라 빨간 것이 정상이다.

test_server.Server(in-process HTTP) 를 그대로 쓴다. 캐시 라우트는 그 잡의 토큰으로만 부른다.
"""

import hashlib
import io
import json
import tarfile
import threading
import time
from pathlib import Path
from typing import Any

import pytest

import remote_ci_monitor.materialize as materialize_mod
from remote_ci_monitor.config import parse_preset
from remote_ci_monitor.materialize import MaterializeError
from test_server import PRESETS, TAR, TREE_HASH, Server, sh

MODE_FILE = 0o100644
MODE_EXEC = 0o100755
BLOBS = {"X-RCM-Tree": "blobs"}
HIGH, NORMAL, LOW = 1, 0, -1
OTHER_TREE = "ab" * 32
THIRD_TREE = "cd" * 32


# ── 도우미 ───────────────────────────────────────────────────────────────────


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def manifest_of(
    files: dict[str, bytes],
    links: dict[str, str] | None = None,
    *,
    exec_paths: tuple[str, ...] = (),
) -> dict[str, Any]:
    """서버가 받는 manifest 본문. 경로순 정렬."""
    return {
        "files": [
            {
                "path": path,
                "mode": MODE_EXEC if path in exec_paths else MODE_FILE,
                "size": len(data),
                "sha256": sha(data),
            }
            for path, data in sorted(files.items())
        ],
        "links": [{"path": p, "target": t} for p, t in sorted((links or {}).items())],
    }


def blob_tar(members: dict[str, bytes]) -> bytes:
    """멤버 이름 = 해시(또는 시험용으로 일부러 틀린 이름) 인 tar.gz."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def blobs_for(files: dict[str, bytes], missing: list[str]) -> bytes:
    by_sha = {sha(d): d for d in files.values()}
    return blob_tar({h: by_sha[h] for h in missing})


def cache_server(tmp_path: Path, *, workers: bool = False, **overrides: Any) -> Server:
    return Server(tmp_path, workers=workers, snapshot_cache=True, **overrides)


def submit(
    srv: Server,
    token: str = "alice",
    *,
    preset: str = "ok",
    tree_hash: str = TREE_HASH,
    priority: Any = None,
    size: int | None = None,
    join: bool | None = None,
) -> tuple[int, Any]:
    body: dict[str, Any] = {
        "preset": preset,
        "inputs": {},
        "source": {
            "mode": "tree",
            "repo": "org/app",
            "base_sha": "abc123f",
            "dirty": True,
            "tree_hash": tree_hash,
            "bytes": size if size is not None else len(TAR),
        },
        "requester_label": f"{token}@host",
    }
    if priority is not None:
        body["priority"] = priority
    if join is not None:
        body["join"] = join
    return srv.req("POST", "/jobs", token=token, json_body=body)


def new_job(srv: Server, token: str = "alice", **kw: Any) -> int:
    status, body = submit(srv, token, **kw)
    assert status == 201, body
    return int(body["job_id"])


def post_manifest(srv: Server, jid: int, manifest: dict[str, Any], token: str = "alice"):
    return srv.req("POST", f"/jobs/{jid}/tree/manifest", token=token, json_body=manifest)


def put_blobs(srv: Server, jid: int, data: bytes, token: str = "alice"):
    return srv.req("PUT", f"/jobs/{jid}/tree", token=token, body=data, headers=BLOBS)


def view(srv: Server, jid: int, token: str | None = None) -> dict[str, Any]:
    status, body = srv.req("GET", f"/jobs/{jid}", token=token)
    assert status == 200, body
    return body


def set_priority(srv: Server, jid: int, value: Any, token: str = "admin"):
    return srv.req("POST", f"/jobs/{jid}/priority", token=token, json_body={"priority": value})


def queue_rows(srv: Server) -> list[dict[str, Any]]:
    return srv.req("GET", "/api/status")[1]["pools"][0]["queue"]


def server_info(srv: Server) -> dict[str, Any]:
    return srv.req("GET", "/api/status")[1]["server"]


def blob_files(srv: Server) -> list[Path]:
    root = srv.cfg.data_dir / "blobs"
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*") if p.is_file())


def blob_files_named(srv: Server, digest: str) -> list[Path]:
    return [p for p in blob_files(srv) if p.name == digest]


def part_files(srv: Server) -> list[Path]:
    return [p for p in blob_files(srv) if p.name.endswith(".part")]


def upload_via_cache(srv: Server, jid: int, files: dict[str, bytes], token: str = "alice"):
    """manifest → missing → blob PUT. (manifest 응답, PUT 응답 또는 None) 을 돌려준다."""
    status, m = post_manifest(srv, jid, manifest_of(files), token=token)
    assert status == 200, m
    if not m["missing"]:
        return m, None
    status, body = put_blobs(srv, jid, blobs_for(files, m["missing"]), token=token)
    assert status == 200, body
    return m, body


@pytest.fixture
def srv(tmp_path):
    s = cache_server(tmp_path)
    yield s
    s.close()


@pytest.fixture
def live(tmp_path):
    s = cache_server(tmp_path, workers=True)
    yield s
    s.close()


# ── 우선순위 · 제출 ───────────────────────────────────────────────────────────


def test_submit_accepts_priority_names_and_ints_and_orders_the_queue(srv):
    status, low = submit(srv, "admin", priority=-1, tree_hash=OTHER_TREE)
    assert status == 201 and low["cache"] is True, low
    normal = new_job(srv, "alice")  # 생략 = 프리셋 기본(normal)
    status, high = submit(srv, "admin", priority="high", tree_hash=THIRD_TREE)
    assert status == 201, high
    assert view(srv, low["job_id"])["priority"] == LOW
    assert view(srv, normal)["priority"] == NORMAL
    assert view(srv, high["job_id"])["priority"] == HIGH
    rows = queue_rows(srv)
    assert [r["id"] for r in rows] == [high["job_id"], normal, low["job_id"]]  # (-priority, id)
    assert [r["position"] for r in rows] == [1, 2, 3]
    assert [r["priority"] for r in rows] == [HIGH, NORMAL, LOW]
    assert all(r["reason"] == "uploading" for r in rows)  # 우선순위는 이유가 아니다


def test_submit_rejects_bad_priority_values(srv):
    for bad in ("urgent", 2, -2, True, [1], {"p": 1}):
        status, body = submit(srv, "admin", priority=bad)
        assert status == 400 and "priority" in body["error"], (bad, body)
    assert queue_rows(srv) == []  # 잡을 만들지 않았다


def test_non_admin_cannot_exceed_the_preset_default(srv):
    status, body = submit(srv, "alice", priority="high")
    assert status == 403 and "admin" in body["error"], body
    assert queue_rows(srv) == []
    status, body = submit(srv, "alice", priority=1)
    assert status == 403
    status, body = submit(srv, "alice", priority="low")
    assert status == 201 and view(srv, body["job_id"])["priority"] == LOW
    status, body = submit(srv, "alice", priority="normal", tree_hash=OTHER_TREE)
    assert status == 201 and view(srv, body["job_id"])["priority"] == NORMAL
    status, body = submit(srv, "admin", priority="high", tree_hash=THIRD_TREE)
    assert status == 201 and view(srv, body["job_id"])["priority"] == HIGH


def test_preset_priority_is_the_default_and_the_ceiling_for_sessions(tmp_path):
    s = cache_server(tmp_path)
    try:
        s.cfg.presets = tuple(
            parse_preset(p) for p in [*PRESETS, sh("urgent-gate", "echo gate", priority="high")]
        )
        jid = new_job(s, "alice", preset="urgent-gate")
        assert view(s, jid)["priority"] == HIGH  # 생략 = 프리셋 기본
        gate = "urgent-gate"
        status, body = submit(s, "alice", preset=gate, priority="high", tree_hash=OTHER_TREE)
        assert status == 201 and view(s, body["job_id"])["priority"] == HIGH  # 같은 값은 된다
        status, body = submit(s, "alice", preset=gate, priority="low", tree_hash=THIRD_TREE)
        assert status == 201 and view(s, body["job_id"])["priority"] == LOW  # 낮추는 건 된다
        status, body = submit(s, "alice", preset="ok", priority="high", tree_hash="ef" * 32)
        assert status == 403  # 다른 프리셋(normal)은 여전히 막힌다
        by_name = {p["name"]: p for p in s.req("GET", "/api/status")[1]["presets"]}
        assert by_name["urgent-gate"]["priority"] == HIGH and by_name["ok"]["priority"] == NORMAL
    finally:
        s.close()


def test_joining_with_a_higher_priority_bumps_the_job_once(srv):
    jid = new_job(srv, "alice")
    status, joined = submit(srv, "admin", priority="high")
    assert status == 200 and joined["joined"] is True and joined["job_id"] == jid
    assert view(srv, jid)["priority"] == HIGH
    status, body = submit(srv, "bob", priority="high")  # 비-admin 은 합류로도 못 올린다
    assert status == 403 and "admin" in body["error"]
    assert [j["name"] for j in view(srv, jid)["joiners"]] == ["macmini-admin"]
    status, again = submit(srv, "alice", priority="low")  # 요청자가 낮게 다시 넣어도 안 내려간다
    assert status == 200 and again["joined"] is True
    assert view(srv, jid)["priority"] == HIGH
    assert [j["name"] for j in view(srv, jid)["joiners"]] == ["macmini-admin"]


# ── 우선순위 · 변경 라우트 ────────────────────────────────────────────────────


def test_priority_route_is_admin_only_and_needs_a_valid_value(srv):
    jid = new_job(srv, "alice")
    assert srv.req("POST", f"/jobs/{jid}/priority", json_body={"priority": "high"})[0] == 401
    assert set_priority(srv, jid, "high", token="alice")[0] == 403  # 요청자라도 admin 이 아니면
    assert set_priority(srv, jid, "high", token="bob")[0] == 403
    status, body = set_priority(srv, jid, "high")
    assert status == 200 and body == {"job_id": jid, "priority": HIGH}
    assert view(srv, jid)["priority"] == HIGH
    status, body = set_priority(srv, jid, -1)
    assert status == 200 and body == {"job_id": jid, "priority": LOW}
    assert view(srv, jid)["priority"] == LOW
    for bad in ("urgent", 5, None, "1x"):
        status, body = set_priority(srv, jid, bad)
        assert status == 400 and "priority" in body["error"], (bad, body)
    assert view(srv, jid)["priority"] == LOW
    assert set_priority(srv, 999, "high")[0] == 404
    assert srv.req("GET", f"/jobs/{jid}/priority", token="admin")[0] == 405


def test_priority_route_reorders_waiting_jobs(srv):
    a = new_job(srv, "alice")
    b = new_job(srv, "bob", tree_hash=OTHER_TREE)
    assert [r["id"] for r in queue_rows(srv)] == [a, b]
    assert set_priority(srv, b, "high")[0] == 200
    rows = queue_rows(srv)
    assert [r["id"] for r in rows] == [b, a] and [r["position"] for r in rows] == [1, 2]
    srv.upload(b, token="bob")  # queued 도 대기 잡 — 여전히 바꿀 수 있다
    assert view(srv, b)["state"] == "queued"
    assert set_priority(srv, b, "normal")[1] == {"job_id": b, "priority": NORMAL}
    assert [r["id"] for r in queue_rows(srv)] == [a, b]


def test_priority_route_is_409_for_running_and_finished_jobs(live):
    jid = live.submit(preset="slow")[1]["job_id"]
    live.upload(jid)
    live.wait_state(jid, "running")
    status, body = set_priority(live, jid, "high")
    assert status == 409 and body["state"] == "running", body
    assert view(live, jid)["priority"] == NORMAL
    live.req("POST", f"/jobs/{jid}/cancel", token="alice", json_body={})
    assert live.wait_terminal(jid).state == "cancelled"
    status, body = set_priority(live, jid, "high")
    assert status == 409 and body["state"] == "cancelled"
    waiting = live.submit(tree_hash=OTHER_TREE)[1]["job_id"]  # uploading
    live.req("POST", f"/jobs/{waiting}/cancel", token="alice", json_body={})
    assert set_priority(live, waiting, "high")[0] == 409


# ── 캐시 · manifest ───────────────────────────────────────────────────────────


def test_manifest_returns_missing_hashes_deduplicated_and_marks_the_job_received(srv):
    jid = new_job(srv)
    files = {"a.txt": b"1", "b.txt": b"1", "sub/c.txt": b"22"}  # a 와 b 는 같은 내용
    status, body = post_manifest(srv, jid, manifest_of(files, {"link": "a.txt"}))
    assert status == 200, body
    assert sorted(body["missing"]) == sorted({sha(b"1"), sha(b"22")})
    assert body["missing_bytes"] == 1 + 2  # 해시별 한 번
    assert body["state"] == "uploading"
    v = view(srv, jid)
    assert v["state"] == "uploading" and v["source"]["last_received_at"] is not None
    assert (srv.app.job_dir(jid) / "manifest.json").exists()
    stored = json.loads((srv.app.job_dir(jid) / "manifest.json").read_text())
    assert [f["path"] for f in stored["files"]] == ["a.txt", "b.txt", "sub/c.txt"]
    assert stored["links"] == [{"path": "link", "target": "a.txt"}]


def test_manifest_needs_the_jobs_token_and_the_cache_enabled(tmp_path, srv):
    jid = new_job(srv)
    m = manifest_of({"a.txt": b"x"})
    assert srv.req("POST", f"/jobs/{jid}/tree/manifest", json_body=m)[0] == 401
    assert post_manifest(srv, jid, m, token="bob")[0] == 403
    assert post_manifest(srv, 999, m)[0] == 404
    assert post_manifest(srv, jid, m, token="admin")[0] == 200  # admin 은 된다
    assert srv.req("GET", f"/jobs/{jid}/tree/manifest", token="alice")[0] == 405
    off = Server(tmp_path / "off", workers=False, snapshot_cache=False)
    try:
        status, body = submit(off, "alice")
        assert status == 201 and body.get("cache", False) is False
        assert post_manifest(off, body["job_id"], m)[0] == 404  # 구버전 서버처럼 보인다
        assert off.store.get_job(body["job_id"]).state == "uploading"
        assert put_blobs(off, body["job_id"], blob_tar({sha(b"x"): b"x"}))[0] in (400, 404, 409)
        assert off.upload(body["job_id"])[0] == 200  # 전체 tar 는 그대로 된다
    finally:
        off.close()


@pytest.mark.parametrize(
    "manifest",
    [
        {"files": [{"path": "/etc/x", "mode": MODE_FILE, "size": 1, "sha256": "a" * 64}]},
        {"files": [{"path": "../x", "mode": MODE_FILE, "size": 1, "sha256": "a" * 64}]},
        {"files": [{"path": "a/../x", "mode": MODE_FILE, "size": 1, "sha256": "a" * 64}]},
        {"files": [{"path": "a//x", "mode": MODE_FILE, "size": 1, "sha256": "a" * 64}]},
        {"files": [{"path": "", "mode": MODE_FILE, "size": 1, "sha256": "a" * 64}]},
        {"files": [{"path": "a/./x", "mode": MODE_FILE, "size": 1, "sha256": "a" * 64}]},
        {"files": [{"path": ".git/HEAD", "mode": MODE_FILE, "size": 1, "sha256": "a" * 64}]},
        {"files": [{"path": "a\\x", "mode": MODE_FILE, "size": 1, "sha256": "a" * 64}]},
        {"files": [{"path": "a\x00x", "mode": MODE_FILE, "size": 1, "sha256": "a" * 64}]},
        {
            "files": [
                {"path": "dup", "mode": MODE_FILE, "size": 1, "sha256": "a" * 64},
                {"path": "dup", "mode": MODE_FILE, "size": 1, "sha256": "b" * 64},
            ]
        },
        {
            "files": [
                {"path": "d", "mode": MODE_FILE, "size": 1, "sha256": "a" * 64},
                {"path": "d/inner", "mode": MODE_FILE, "size": 1, "sha256": "b" * 64},
            ]
        },
        {
            "files": [{"path": "same", "mode": MODE_FILE, "size": 1, "sha256": "a" * 64}],
            "links": [{"path": "same", "target": "x"}],
        },
        {"files": [{"path": "x", "mode": MODE_FILE, "size": 1, "sha256": "zz" * 32}]},
        {"files": [{"path": "x", "mode": MODE_FILE, "size": 1, "sha256": "A" * 64}]},
        {"files": [{"path": "x", "mode": MODE_FILE, "size": 1, "sha256": "a" * 63}]},
        {"files": [{"path": "x", "mode": MODE_FILE, "size": -1, "sha256": "a" * 64}]},
        {"files": [{"path": "x", "mode": MODE_FILE, "size": "1", "sha256": "a" * 64}]},
        {"files": [{"path": "x", "mode": "rwx", "size": 1, "sha256": "a" * 64}]},
        {"files": [{"path": "x", "size": 1, "sha256": "a" * 64}]},
        {"files": [{"path": "x", "mode": MODE_FILE, "size": 1}]},
        {"files": "not a list"},
        {"files": [], "links": [{"path": "l", "target": "/etc/passwd"}]},
        {"files": [], "links": [{"path": "l", "target": "../outside"}]},
        {"files": [], "links": [{"path": "../l", "target": "x"}]},
        {"files": [], "links": [{"path": "l"}]},
        {"files": [], "links": "nope"},
        [],
    ],
)
def test_manifest_validation_rejects_bad_paths_hashes_sizes_and_links(srv, manifest):
    jid = new_job(srv)
    status, body = post_manifest(srv, jid, manifest)
    assert status == 400, (manifest, status, body)
    assert set(body) == {"error"}
    j = srv.store.get_job(jid)
    assert j.state == "uploading"  # 검증 실패는 잡을 죽이지 않는다 — 다시 보낼 수 있다
    assert not (srv.app.job_dir(jid) / "manifest.json").exists()
    assert blob_files(srv) == []


def test_manifest_over_the_snapshot_limit_is_413_and_cancels_the_job(srv):
    jid = new_job(srv)
    m = manifest_of({"a": b"x", "b": b"y"})
    m["files"][0]["size"] = 15_000
    m["files"][1]["size"] = 5_000  # 합계 20 KB > 10 KB
    status, body = post_manifest(srv, jid, m)
    assert status == 413 and "exceeds" in body["error"] and ".rcmignore" in body["error"], body
    j = srv.store.get_job(jid)
    assert j.state == "cancelled" and j.summary == "snapshot 20 KB exceeds 10 KB"
    assert j.cancelled_by == "server"
    assert post_manifest(srv, jid, manifest_of({"a": b"x"}))[0] == 409  # 이미 cancelled
    assert view(srv, jid)["state"] == "cancelled"


def test_manifest_on_a_job_that_is_not_uploading_is_409(srv):
    jid = new_job(srv)
    assert srv.req("POST", f"/jobs/{jid}/cancel", token="alice", json_body={})[0] == 200
    status, body = post_manifest(srv, jid, manifest_of({"a": b"x"}))
    assert status == 409 and body["state"] == "cancelled", body
    queued = new_job(srv, tree_hash=OTHER_TREE)
    assert srv.upload(queued)[0] == 200
    status, body = post_manifest(srv, queued, manifest_of({"a": b"x"}))
    assert status == 409 and body["state"] == "queued", body


def test_manifest_with_nothing_missing_queues_the_job_without_a_put(srv):
    files = {"hello.txt": b"hello\n", "x/y.bin": b"\x00\x01\x02"}
    first = new_job(srv, "alice")
    m, put = upload_via_cache(srv, first, files)
    assert put is not None and put["state"] == "queued"
    second = new_job(srv, "bob", preset="bad")  # 다른 프리셋 → 합류 아님 · 같은 파일
    status, body = post_manifest(srv, second, manifest_of(files), token="bob")
    assert status == 200, body
    assert body == {"missing": [], "missing_bytes": 0, "state": "queued"}
    v = view(srv, second)
    assert v["state"] == "queued" and v["queued_at"] is not None
    assert v["source"]["uploaded_bytes"] == 0
    assert v["source"]["cached_bytes"] == 6 + 3
    assert put_blobs(srv, second, blob_tar({}), token="bob")[0] == 409  # 이미 queued
    assert [r["state"] for r in queue_rows(srv)] == ["queued", "queued"]


def attr_of(row: Any, name: str) -> Any:
    """blob 행이 dataclass 든 매핑이든 같은 이름으로 읽는다(모양은 구현이 정한다)."""
    return getattr(row, name) if hasattr(row, name) else row[name]


def test_manifest_touches_last_used_at_of_referenced_blobs(srv):
    files = {"hello.txt": b"hello\n"}
    first = new_job(srv, "alice")
    upload_via_cache(srv, first, files)
    (before,) = srv.store.list_blobs()
    time.sleep(0.05)  # 시각은 float 초 — 이만큼이면 확실히 뒤다
    second = new_job(srv, "bob", preset="bad")
    assert post_manifest(srv, second, manifest_of(files), token="bob")[0] == 200
    (after,) = srv.store.list_blobs()
    assert attr_of(after, "sha256") == attr_of(before, "sha256") == sha(b"hello\n")
    assert attr_of(after, "last_used_at") > attr_of(before, "last_used_at")
    assert attr_of(after, "size") == attr_of(before, "size") == 6


def test_large_manifest_is_not_limited_by_the_json_body_cap(srv):
    files = {f"src/module_{i:04d}/file_{i:04d}.py": f"x = {i}\n".encode() for i in range(800)}
    m = manifest_of(files)
    assert len(json.dumps(m).encode()) > 64 * 1024  # 일반 JSON 상한(64 KB)보다 크다
    jid = new_job(srv)
    status, body = post_manifest(srv, jid, m)
    assert status == 200, body
    assert len(body["missing"]) == 800


# ── 캐시 · blob PUT ───────────────────────────────────────────────────────────


def test_blob_put_stores_verified_blobs_and_queues_the_job(srv):
    jid = new_job(srv)
    files = {"hello.txt": b"hello\n", "bin/run.sh": b"#!/bin/sh\necho ran\n"}
    m = manifest_of(files, exec_paths=("bin/run.sh",))
    status, body = post_manifest(srv, jid, m)
    assert status == 200 and len(body["missing"]) == 2
    tar = blobs_for(files, body["missing"])
    status, body = put_blobs(srv, jid, tar)
    assert status == 200, body
    assert body["job_id"] == jid and body["state"] == "queued"
    v = view(srv, jid)
    assert v["state"] == "queued" and v["queued_at"] is not None
    assert v["source"]["uploaded_bytes"] == len(tar)  # 실제로 받은 HTTP 본문 바이트
    assert v["source"]["cached_bytes"] == 0
    assert v["source"]["received_bytes"] is not None  # 기존 키는 그대로
    for data in files.values():
        stored = blob_files_named(srv, sha(data))
        assert len(stored) == 1 and stored[0].read_bytes() == data
    assert part_files(srv) == []
    rows = srv.store.list_blobs()
    assert len(rows) == 2
    info = server_info(srv)
    assert info["snapshot_cache"] == {"blobs": 2, "bytes": sum(len(d) for d in files.values())}
    assert not (srv.app.job_dir(jid) / "tree.tar.gz").exists()  # 전체 tar 는 없다


def test_blob_hash_mismatch_is_400_and_cancels_the_job(srv):
    jid = new_job(srv)
    good = b"hello\n"
    status, body = post_manifest(srv, jid, manifest_of({"hello.txt": good}))
    assert status == 200 and body["missing"] == [sha(good)]
    status, body = put_blobs(srv, jid, blob_tar({sha(good): b"tampered\n"}))
    assert status == 400 and "hash mismatch" in body["error"], body
    j = srv.store.get_job(jid)
    assert j.state == "cancelled" and "blob hash mismatch" in (j.summary or "")
    assert j.summary.startswith("snapshot rejected")
    assert blob_files_named(srv, sha(good)) == []  # 틀린 내용은 저장하지 않는다
    assert blob_files_named(srv, sha(b"tampered\n")) == []
    assert part_files(srv) == []
    assert srv.store.list_blobs() == []
    assert server_info(srv)["snapshot_cache"] == {"blobs": 0, "bytes": 0}
    assert put_blobs(srv, jid, blob_tar({sha(good): good}))[0] == 409  # 이미 cancelled


def test_blob_not_in_missing_is_400_and_not_stored(srv):
    jid = new_job(srv)
    files = {"a.txt": b"aaa"}
    status, body = post_manifest(srv, jid, manifest_of(files))
    assert status == 200
    extra = b"not in the manifest"
    status, body = put_blobs(srv, jid, blob_tar({sha(b"aaa"): b"aaa", sha(extra): extra}))
    assert status == 400, body
    assert blob_files_named(srv, sha(extra)) == []
    j = srv.store.get_job(jid)
    assert j.state == "cancelled" and (j.summary or "").startswith("snapshot rejected")
    assert part_files(srv) == []


def test_blob_member_larger_than_declared_is_rejected_without_storing_it(srv):
    jid = new_job(srv)
    zeros = b"\x00" * 200_000  # gzip 으로 수백 바이트 — Content-Length 상한(10 KB)은 통과한다
    m = manifest_of({"big.bin": zeros})
    m["files"][0]["size"] = 100  # manifest 는 100 바이트라고 거짓말한다
    status, body = post_manifest(srv, jid, m)
    assert status == 200 and body["missing_bytes"] == 100
    tar = blob_tar({sha(zeros): zeros})
    assert len(tar) < 10_000
    status, body = put_blobs(srv, jid, tar)
    assert status in (400, 413), body
    assert blob_files_named(srv, sha(zeros)) == []
    assert part_files(srv) == []
    assert srv.store.get_job(jid).state == "cancelled"
    assert srv.store.list_blobs() == []


def test_blob_put_ignores_paths_in_member_names_and_rejects_non_hash_names(srv):
    jid = new_job(srv)
    data = b"hello\n"
    assert post_manifest(srv, jid, manifest_of({"hello.txt": data}))[0] == 200
    status, body = put_blobs(srv, jid, blob_tar({"../" + sha(data): data}))
    assert status == 400, body
    assert blob_files_named(srv, sha(data)) == []
    assert not list(srv.cfg.data_dir.parent.glob(sha(data)))
    assert part_files(srv) == []


def test_blob_put_before_a_manifest_is_rejected(srv):
    jid = new_job(srv)
    data = b"hello\n"
    status, body = put_blobs(srv, jid, blob_tar({sha(data): data}))
    assert status in (400, 409), body
    assert srv.store.get_job(jid).state == "uploading"
    assert srv.upload(jid)[0] == 200  # 전체 tar 경로는 여전히 열려 있다


def test_blob_put_needs_the_jobs_token_and_content_length(srv):
    jid = new_job(srv)
    data = b"hello\n"
    assert post_manifest(srv, jid, manifest_of({"hello.txt": data}))[0] == 200
    tar = blob_tar({sha(data): data})
    assert srv.req("PUT", f"/jobs/{jid}/tree", body=tar, headers=BLOBS)[0] == 401
    assert put_blobs(srv, jid, tar, token="bob")[0] == 403
    status, _ = srv.req(
        "PUT",
        f"/jobs/{jid}/tree",
        token="alice",
        body=tar,
        headers={**BLOBS, "Transfer-Encoding": "chunked"},
    )
    assert status == 411
    assert srv.store.get_job(jid).state == "uploading"
    assert put_blobs(srv, jid, tar)[0] == 200


def test_blob_put_over_the_snapshot_limit_is_413_and_cancels(srv):
    jid = new_job(srv)
    data = b"hello\n"
    assert post_manifest(srv, jid, manifest_of({"hello.txt": data}))[0] == 200
    status, body = put_blobs(srv, jid, b"x" * 20_000)  # Content-Length 가 상한을 넘는다
    assert status == 413 and "exceeds" in body["error"]
    j = srv.store.get_job(jid)
    assert j.state == "cancelled" and j.summary == "snapshot 20 KB exceeds 10 KB"


def test_same_blob_uploaded_concurrently_for_two_jobs_succeeds_once_on_disk(srv):
    data = b"shared content\n" * 50
    files = {"shared.txt": data}
    a = new_job(srv, "alice", preset="ok")
    b = new_job(srv, "bob", preset="bad")
    for jid, token in ((a, "alice"), (b, "bob")):
        status, body = post_manifest(srv, jid, manifest_of(files), token=token)
        assert status == 200 and body["missing"] == [sha(data)], body
    tar = blob_tar({sha(data): data})
    results: dict[str, tuple[int, Any]] = {}
    barrier = threading.Barrier(2)

    def put(jid: int, token: str) -> None:
        barrier.wait(timeout=5)
        results[token] = put_blobs(srv, jid, tar, token=token)

    threads = [
        threading.Thread(target=put, args=(a, "alice")),
        threading.Thread(target=put, args=(b, "bob")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    assert all(not t.is_alive() for t in threads)
    assert results["alice"][0] == 200 and results["bob"][0] == 200, results
    stored = blob_files_named(srv, sha(data))
    assert len(stored) == 1 and stored[0].read_bytes() == data
    assert part_files(srv) == []
    assert len(srv.store.list_blobs()) == 1
    assert srv.store.get_job(a).state == "queued" and srv.store.get_job(b).state == "queued"
    assert server_info(srv)["snapshot_cache"] == {"blobs": 1, "bytes": len(data)}
    assert server_info(srv)["last_error"] is None


def test_legacy_full_tar_upload_still_works_when_cache_is_on(srv):
    jid = new_job(srv)
    status, body = srv.upload(jid)
    assert status == 200 and body["state"] == "queued"
    assert (srv.app.job_dir(jid) / "tree.tar.gz").exists()
    src = view(srv, jid)["source"]
    assert "uploaded_bytes" in src and "cached_bytes" in src  # 추가 키는 tree 잡에 항상 있다
    assert src["received_bytes"] == len(TAR)
    assert blob_files(srv) == []  # 전체 tar 는 blob 을 만들지 않는다


# ── 캐시 범위 · 상태 카운터 ───────────────────────────────────────────────────


def test_token_scope_keeps_missing_lists_apart(tmp_path):
    s = cache_server(tmp_path, snapshot_cache_scope="token")
    try:
        data = b"secret build input\n"
        files = {"in.txt": data}
        first = new_job(s, "alice")
        upload_via_cache(s, first, files)
        bob = new_job(s, "bob", preset="bad")
        status, body = post_manifest(s, bob, manifest_of(files), token="bob")
        assert status == 200 and body["missing"] == [sha(data)]  # 남의 blob 은 안 보인다
        assert body["state"] == "uploading"
        again = new_job(s, "alice", preset="bad", join=False)  # bob 의 같은 잡에 합류하지 않게
        status, body = post_manifest(s, again, manifest_of(files))
        assert status == 200 and body["missing"] == [] and body["state"] == "queued"
        status, body = put_blobs(s, bob, blob_tar({sha(data): data}), token="bob")
        assert status == 200 and body["state"] == "queued"
        assert len(blob_files_named(s, sha(data))) == 2  # 토큰별 사본
        assert len(s.store.list_blobs()) == 2
        assert s.store.get_job(bob).state == "queued"
    finally:
        s.close()


def test_global_scope_shares_blobs_between_tokens(srv):
    data = b"shared\n"
    files = {"in.txt": data}
    first = new_job(srv, "alice")
    upload_via_cache(srv, first, files)
    bob = new_job(srv, "bob", preset="bad")
    status, body = post_manifest(srv, bob, manifest_of(files), token="bob")
    assert status == 200 and body["missing"] == [] and body["state"] == "queued"
    assert len(blob_files(srv)) == 1


def test_status_reports_cache_counters_and_notify_failures(tmp_path, srv):
    info = server_info(srv)
    assert info["snapshot_cache"] == {"blobs": 0, "bytes": 0}
    assert info["notify_failures"] == 0
    files = {"a": b"12345", "b": b"678"}
    upload_via_cache(srv, new_job(srv), files)
    assert server_info(srv)["snapshot_cache"] == {"blobs": 2, "bytes": 8}
    doc = srv.req("GET", "/api/status")[1]
    assert doc["schema_version"] == 1  # 추가 키만 — 버전은 그대로
    off = Server(tmp_path / "off", workers=False, snapshot_cache=False)
    try:
        assert server_info(off)["snapshot_cache"] is None  # 꺼짐은 0 개가 아니다
        assert server_info(off)["notify_failures"] == 0
    finally:
        off.close()


# ── 자재화 ───────────────────────────────────────────────────────────────────


def write_blob_store(root: Path, files: dict[str, bytes]) -> Path:
    blobs = root / "blobs"
    for data in files.values():
        h = sha(data)
        p = blobs / h[:2] / h
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return blobs


def test_assemble_from_manifest_copies_blobs_applies_modes_and_links(tmp_path):
    files = {"hello.txt": b"hello\n", "bin/run.sh": b"#!/bin/sh\necho ran\n", "deep/a/b/c": b"c"}
    blobs = write_blob_store(tmp_path, files)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(manifest_of(files, {"link.txt": "hello.txt"}, exec_paths=("bin/run.sh",)))
    )
    ws = tmp_path / "workspaces" / "7"
    materialize_mod.assemble_from_manifest(manifest, blobs, ws)
    for path, data in files.items():
        assert (ws / path).read_bytes() == data
    assert (ws / "bin/run.sh").stat().st_mode & 0o111
    assert not (ws / "hello.txt").stat().st_mode & 0o111
    assert (ws / "link.txt").is_symlink() and (ws / "link.txt").readlink() == Path("hello.txt")
    # 복사이지 하드링크가 아니다 — 워크스페이스를 고쳐도 blob 은 그대로
    (ws / "hello.txt").write_bytes(b"changed\n")
    h = sha(b"hello\n")
    assert (blobs / h[:2] / h).read_bytes() == b"hello\n"
    assert (blobs / h[:2] / h).stat().st_nlink == 1


def test_assemble_from_manifest_fails_on_a_missing_blob_naming_it(tmp_path):
    files = {"hello.txt": b"hello\n", "gone.txt": b"gone\n"}
    blobs = write_blob_store(tmp_path, {"hello.txt": b"hello\n"})  # gone 은 blob 이 없다
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(manifest_of(files)))
    ws = tmp_path / "workspaces" / "8"
    with pytest.raises(MaterializeError) as e:
        materialize_mod.assemble_from_manifest(manifest, blobs, ws)
    msg = str(e.value)
    assert "blob missing" in msg and sha(b"gone\n")[:7] in msg
    assert str(tmp_path) not in msg  # 경로 없음
    assert not ws.exists() or not any(ws.iterdir())  # 반쯤 만든 워크스페이스를 남기지 않는다


def test_worker_runs_a_cached_job_from_blobs_without_touching_them(tmp_path):
    presets = [
        *PRESETS,
        sh(
            "cached",
            "cat hello.txt; ./run.sh; echo more >> hello.txt; test -L link.txt && cat link.txt; "
            "echo '::rcm::summary::from blobs'",
        ),
    ]
    s = cache_server(tmp_path)
    try:
        s.cfg.presets = tuple(parse_preset(p) for p in presets)
        s.app.start()
        jid = new_job(s, preset="cached")
        files = {"hello.txt": b"hello\n", "run.sh": b"#!/bin/sh\necho ran\n"}
        m = manifest_of(files, {"link.txt": "hello.txt"}, exec_paths=("run.sh",))
        status, body = post_manifest(s, jid, m)
        assert status == 200
        assert put_blobs(s, jid, blobs_for(files, body["missing"]))[0] == 200
        j = s.wait_terminal(jid)
        assert j.state == "succeeded" and j.summary == "from blobs", j.summary
        log = s.app.log_path(jid).read_text()
        assert "hello\nran\n" in log and log.count("hello\n") >= 2
        h = sha(b"hello\n")
        assert blob_files_named(s, h)[0].read_bytes() == b"hello\n"  # 잡이 고쳐도 blob 은 그대로
        assert [t["state"] for t in view(s, jid)["transitions"]] == [
            "uploading",
            "queued",
            "running",
            "succeeded",
        ]
    finally:
        s.close()


def test_missing_blob_at_run_time_fails_the_job_with_blob_missing(live):
    assert live.req("POST", "/pause", token="admin", json_body={})[0] == 200
    jid = new_job(live)
    files = {"hello.txt": b"hello\n"}
    m, put = upload_via_cache(live, jid, files)
    assert put is not None and put["state"] == "queued"
    for p in blob_files_named(live, sha(b"hello\n")):
        p.unlink()  # 정리가 지웠거나 손상됐다
    assert live.req("POST", "/resume", token="admin", json_body={})[0] == 200
    j = live.wait_terminal(jid)
    assert j.state == "failed" and j.exit_code is None
    assert "blob missing" in (j.summary or ""), j.summary
    v = view(live, jid)
    assert v["state"] == "failed" and "blob missing" in v["summary"]
    # 잡 실패는 서버 오류가 아니다
    assert live.req("GET", "/api/status")[1]["server"]["last_error"] is None
