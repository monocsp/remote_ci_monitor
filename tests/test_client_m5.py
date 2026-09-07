"""클라이언트(M5a) — `Snapshot.entries`(manifest·tar 의 단일 출처) · `Client.manifest` ·
`Client.upload_blobs` · `upload_cached`(manifest → missing → blob tar → PUT) · 전송 바이트 계측 ·
manifest 404 일 때만 전체 tar 폴백 · 400/403/413 은 ClientError 로 그대로.
명세는 docs/m5-workplan.md M5a-2 「클라이언트」. 아직 구현 전이라 빨간 것이 정상이다.

전송 바이트는 두 곳에서 잰다: 클라이언트 `_request` 를 감싸 요청 본문 길이를 기록하고, 서버가 센
`source.uploaded_bytes` 를 읽는다. 난수 내용(압축 안 됨)으로 gzip 이 만드는 허위 통과를 막는다.
"""

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

import remote_ci_monitor.client as client_mod
from remote_ci_monitor.client import Client, ClientError, make_snapshot
from test_server import Server

MODE_FILE = 0o100644
MODE_EXEC = 0o100755
MODE_LINK = 0o120000
MB = 1_000_000


# ── 도우미 ───────────────────────────────────────────────────────────────────


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def attr_of(obj: Any, name: str) -> Any:
    """entries 항목이 dataclass 든 dict 든 같은 이름으로 읽는다."""
    return getattr(obj, name) if hasattr(obj, name) else obj[name]


def cleanup(snap: Any) -> None:
    path = getattr(snap, "tar_path", None)
    if path:
        Path(path).unlink(missing_ok=True)


def write_tree(
    root: Path,
    files: dict[str, bytes],
    *,
    exec_paths: tuple[str, ...] = (),
    links: dict[str, str] | None = None,
) -> int:
    """파일을 쓰고 합계 바이트를 돌려준다."""
    total = 0
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        if rel in exec_paths:
            os.chmod(p, 0o755)
        total += len(data)
    for rel, target in (links or {}).items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, root / rel)
    return total


def random_tree(root: Path, count: int, each: int) -> int:
    root.mkdir(parents=True, exist_ok=True)
    return write_tree(root, {f"assets/blob_{i:02d}.bin": os.urandom(each) for i in range(count)})


def source_of(snap: Any) -> dict[str, Any]:
    return {
        "mode": "tree",
        "repo": snap.repo,
        "base_sha": snap.base_sha,
        "dirty": snap.dirty,
        "tree_hash": snap.tree_hash,
        "bytes": snap.bytes,
    }


class Recorder:
    """`Client._request` 를 감싸 (method, path, 요청 본문 바이트) 를 기록한다."""

    def __init__(self, client: Client):
        self.calls: list[tuple[str, str, int]] = []
        self._orig = client._request
        client._request = self  # type: ignore[method-assign]

    def __call__(self, method: str, path: str, **kw: Any):
        n = kw.get("content_length")
        if n is None and kw.get("json_body") is not None:
            n = len(json.dumps(kw["json_body"]).encode())
        elif n is None and kw.get("data") is not None:
            data = kw["data"]
            try:
                n = len(data)
            except TypeError:
                n = 0
        self.calls.append((method, path, int(n or 0)))
        return self._orig(method, path, **kw)

    def puts(self) -> list[tuple[str, str, int]]:
        return [c for c in self.calls if c[0] == "PUT"]

    def manifest_bytes(self) -> int:
        return sum(n for m, p, n in self.calls if m == "POST" and p.endswith("/tree/manifest"))


def submit_tree(client: Client, snap: Any, preset: str = "ok", *, join: bool = True) -> int:
    resp = client.submit(preset, {}, source_of(snap), requester_label="alice@laptop", join=join)
    assert resp["joined"] is False, resp
    assert resp["cache"] is True
    return int(resp["job_id"])


def source_view(srv: Server, jid: int) -> dict[str, Any]:
    status, body = srv.req("GET", f"/jobs/{jid}")
    assert status == 200, body
    return body["source"]


def big_server(tmp_path: Path, **overrides: Any) -> Server:
    return Server(
        tmp_path, workers=False, snapshot_cache=True, max_snapshot_bytes=256 * MB, **overrides
    )


@pytest.fixture
def srv(tmp_path):
    s = big_server(tmp_path)
    yield s
    s.close()


def client_for(srv: Server, token: str = "alice") -> Client:
    return Client(f"http://127.0.0.1:{srv.port}", srv.tokens[token])


# ── Snapshot.entries ─────────────────────────────────────────────────────────


def test_snapshot_entries_describe_files_executables_and_links(tmp_path):
    root = tmp_path / "plain"
    root.mkdir()
    files = {"a.txt": b"alpha\n", "bin/run.sh": b"#!/bin/sh\necho hi\n", "sub/deep/z": b""}
    write_tree(root, files, exec_paths=("bin/run.sh",), links={"link.txt": "a.txt"})
    snap = make_snapshot(root, tar_dir=tmp_path)
    try:
        entries = list(snap.entries)
        by_path = {attr_of(e, "path"): e for e in entries}
        assert [attr_of(e, "path") for e in entries] == sorted(by_path)  # 경로순
        assert set(by_path) == {"a.txt", "bin/run.sh", "sub/deep/z", "link.txt"}
        assert list(snap.files) == sorted(by_path)  # files 와 같은 목록
        for rel, data in files.items():
            e = by_path[rel]
            assert attr_of(e, "kind") == "file"
            assert attr_of(e, "size") == len(data)
            assert attr_of(e, "sha256") == sha(data)
        assert attr_of(by_path["a.txt"], "mode") == MODE_FILE
        assert attr_of(by_path["bin/run.sh"], "mode") == MODE_EXEC
        assert attr_of(by_path["sub/deep/z"], "size") == 0
        link = by_path["link.txt"]
        assert attr_of(link, "kind") == "link" and attr_of(link, "mode") == MODE_LINK
        assert attr_of(link, "target") == "a.txt"
        # tree_hash 는 entries 에서 나온다 — 내용이 바뀌면 다르다
        (root / "a.txt").write_bytes(b"beta\n")
        again = make_snapshot(root, tar_dir=tmp_path)
        try:
            assert again.tree_hash != snap.tree_hash
            again_by_path = {attr_of(e, "path"): e for e in again.entries}
            assert attr_of(again_by_path["a.txt"], "sha256") == sha(b"beta\n")
        finally:
            cleanup(again)
    finally:
        cleanup(snap)


# ── manifest · upload_blobs ──────────────────────────────────────────────────


def test_manifest_and_upload_blobs_round_trip(srv, tmp_path):
    root = tmp_path / "tree"
    root.mkdir()
    files = {"hello.txt": b"hello\n", "data.bin": os.urandom(3000), "same.txt": b"hello\n"}
    total = write_tree(root, files)
    client = client_for(srv)
    snap = make_snapshot(root, tar_dir=tmp_path)
    try:
        jid = submit_tree(client, snap)
        m = client.manifest(jid, snap)
        assert set(m["missing"]) == {sha(b"hello\n"), sha(files["data.bin"])}  # 중복 제거
        assert m["missing_bytes"] == 6 + 3000
        assert m["state"] == "uploading"
        put = client.upload_blobs(jid, snap, m["missing"])
        assert put["job_id"] == jid and put["state"] == "queued"
        src = source_view(srv, jid)
        assert src["cached_bytes"] == 0
        assert 3000 < src["uploaded_bytes"] < total + 2000  # blob tar(난수 3 KB + 작은 파일)
        assert srv.store.get_job(jid).state == "queued"
    finally:
        cleanup(snap)


# ── upload_cached ─────────────────────────────────────────────────────────────


def test_upload_cached_sends_everything_first_then_nothing_for_an_unchanged_tree(srv, tmp_path):
    root = tmp_path / "tree"
    total = random_tree(root, count=3, each=200_000)
    client = client_for(srv)
    rec = Recorder(client)
    snap = make_snapshot(root, tar_dir=tmp_path)
    try:
        first = submit_tree(client, snap)
        progress: list[tuple[int, int]] = []
        result = client_mod.upload_cached(
            client, first, snap, progress=lambda s, t: progress.append((s, t))
        )
        assert result["job_id"] == first and result["state"] == "queued"
        src = source_view(srv, first)
        assert src["uploaded_bytes"] >= total * 0.95 and src["cached_bytes"] == 0
        assert len(rec.puts()) == 1 and rec.puts()[0][2] >= total * 0.95
        assert progress and progress[-1][0] == progress[-1][1] > 0
        assert [s for s, _ in progress] == sorted(s for s, _ in progress)
        # 같은 트리 두 번째 — 합류를 끄고 다시 제출하면 manifest 만 오간다
        rec.calls.clear()
        second = submit_tree(client, snap, join=False)
        result = client_mod.upload_cached(client, second, snap)
        assert result["job_id"] == second and result["state"] == "queued"
        assert rec.puts() == []  # PUT 자체가 없다
        assert rec.manifest_bytes() > 0
        src = source_view(srv, second)
        assert src["uploaded_bytes"] == 0 and src["cached_bytes"] == total
        assert srv.store.get_job(second).state == "queued"
        assert not (srv.app.job_dir(second) / "tree.tar.gz").exists()
    finally:
        cleanup(snap)


def test_upload_cached_sends_only_the_changed_file(srv, tmp_path):
    root = tmp_path / "tree"
    random_tree(root, count=3, each=MB)
    client = client_for(srv)
    rec = Recorder(client)
    snap = make_snapshot(root, tar_dir=tmp_path)
    try:
        client_mod.upload_cached(client, submit_tree(client, snap), snap)
    finally:
        cleanup(snap)
    (root / "assets" / "blob_01.bin").write_bytes(os.urandom(MB))  # 하나만 바뀐다
    rec.calls.clear()
    snap2 = make_snapshot(root, tar_dir=tmp_path)
    try:
        jid = submit_tree(client, snap2)
        client_mod.upload_cached(client, jid, snap2)
        src = source_view(srv, jid)
        assert MB * 0.98 <= src["uploaded_bytes"] <= MB * 1.05, src  # ≈ 바뀐 파일 하나
        assert src["cached_bytes"] == 2 * MB
        puts = rec.puts()
        assert len(puts) == 1 and MB * 0.98 <= puts[0][2] <= MB * 1.05
    finally:
        cleanup(snap2)


def test_second_upload_of_a_50mb_random_tree_moves_under_ten_percent(srv, tmp_path):
    """완료 기준 ②: 같은 트리를 두 번째 올릴 때 전송 바이트(manifest + PUT)가 트리의 10% 미만."""
    root = tmp_path / "tree"
    total = random_tree(root, count=20, each=2_500_000)  # 50 MB · 압축 안 됨
    client = client_for(srv)
    rec = Recorder(client)
    snap = make_snapshot(root, tar_dir=tmp_path)
    try:
        first = submit_tree(client, snap)
        client_mod.upload_cached(client, first, snap)
        first_src = source_view(srv, first)
        assert first_src["uploaded_bytes"] >= total * 0.95  # 첫 업로드는 진짜 다 보낸다
        rec.calls.clear()
        second = submit_tree(client, snap, join=False)
        client_mod.upload_cached(client, second, snap)
        src = source_view(srv, second)
        sent = src["uploaded_bytes"] + rec.manifest_bytes()
        assert sent < total * 0.10, (sent, total)
        assert src["cached_bytes"] == total
        client_sent = sum(n for _, _, n in rec.calls)
        assert client_sent < total * 0.10  # 클라이언트가 센 값도 같은 결론
    finally:
        cleanup(snap)


def test_manifest_404_falls_back_to_the_full_tar(tmp_path):
    old = Server(tmp_path, workers=False, snapshot_cache=False, max_snapshot_bytes=64 * MB)
    try:
        root = tmp_path / "tree"
        root.mkdir()
        write_tree(root, {"hello.txt": b"hello\n", "x.bin": os.urandom(1000)})
        client = client_for(old)
        rec = Recorder(client)
        snap = make_snapshot(root, tar_dir=tmp_path)
        try:
            resp = client.submit("ok", {}, source_of(snap), requester_label="alice@laptop")
            assert resp.get("cache", False) is False
            jid = int(resp["job_id"])
            result = client_mod.upload_cached(client, jid, snap)
            assert result["job_id"] == jid and result["state"] == "queued"
            assert [m for m, _, _ in rec.calls] == ["POST", "PUT"]  # manifest 404 → 전체 tar 한 번
            assert (old.app.job_dir(jid) / "tree.tar.gz").exists()
            assert not (old.app.job_dir(jid) / "manifest.json").exists()
            assert old.store.get_job(jid).state == "queued"
        finally:
            cleanup(snap)
    finally:
        old.close()


def test_413_from_the_manifest_raises_and_never_falls_back(tmp_path):
    small = Server(tmp_path, workers=False, snapshot_cache=True, max_snapshot_bytes=10_000)
    try:
        root = tmp_path / "tree"
        root.mkdir()
        write_tree(root, {"big.bin": os.urandom(20_000)})
        client = client_for(small)
        rec = Recorder(client)
        snap = make_snapshot(root, tar_dir=tmp_path)
        try:
            resp = client.submit(
                "ok", {}, {**source_of(snap), "bytes": 100}, requester_label="alice@laptop"
            )
            jid = int(resp["job_id"])
            with pytest.raises(ClientError) as e:
                client_mod.upload_cached(client, jid, snap)
            assert e.value.status == 413 and "exceeds" in e.value.message
            assert rec.puts() == []  # 폴백하지 않았다
            j = small.store.get_job(jid)
            assert j.state == "cancelled" and "exceeds" in (j.summary or "")
            assert not (small.app.job_dir(jid) / "tree.tar.gz").exists()
        finally:
            cleanup(snap)
    finally:
        small.close()


def test_403_from_the_manifest_raises_and_never_falls_back(srv, tmp_path):
    root = tmp_path / "tree"
    root.mkdir()
    write_tree(root, {"hello.txt": b"hello\n"})
    alice = client_for(srv, "alice")
    bob = client_for(srv, "bob")
    rec = Recorder(bob)
    snap = make_snapshot(root, tar_dir=tmp_path)
    try:
        jid = submit_tree(alice, snap)
        with pytest.raises(ClientError) as e:
            client_mod.upload_cached(bob, jid, snap)  # 남의 잡
        assert e.value.status == 403
        assert rec.puts() == []
        assert srv.store.get_job(jid).state == "uploading"  # 잡은 그대로 — alice 가 이어서 올린다
        assert client_mod.upload_cached(alice, jid, snap)["state"] == "queued"
    finally:
        cleanup(snap)


def test_hash_mismatch_400_raises_and_leaves_the_job_cancelled(srv, tmp_path, monkeypatch):
    """서버가 400 으로 거부하면(해시 불일치) 조용히 전체 tar 로 가지 않는다."""
    root = tmp_path / "tree"
    root.mkdir()
    write_tree(root, {"hello.txt": b"hello\n"})
    client = client_for(srv)
    rec = Recorder(client)
    snap = make_snapshot(root, tar_dir=tmp_path)
    try:
        jid = submit_tree(client, snap)
        (root / "hello.txt").write_bytes(b"changed after the manifest\n")  # tar ≠ manifest
        with pytest.raises(ClientError) as e:
            client_mod.upload_cached(client, jid, snap)
        assert e.value.status == 400
        assert len(rec.puts()) == 1  # blob PUT 한 번뿐, 전체 tar 재시도 없음
        j = srv.store.get_job(jid)
        assert j.state == "cancelled" and "hash mismatch" in (j.summary or "")
    finally:
        cleanup(snap)
