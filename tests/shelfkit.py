"""보관함(M5p) 서버 시험의 공통 준비 — `docs/m5p-test-scenarios-b.md` 「공통 준비」.

- 서버는 `test_worker_api.WorkerServer`(in-process HTTP · 주입 시계 · 토큰). copier 는 `App.start`
  가 띄우므로 보관을 기다리는 시험은 `workers=True` 로 띄운다.
- 잡을 끝까지 돌리는 길은 둘이다.
  - **로컬 레인**(`run_local`): 진짜 스크립트가 트리의 픽스처를 `out/` 에 만들고, 진짜 수집기가
    묶음을 만든다. 베스트 · 펼치기 · 악의적 멤버를 「진짜 통로」로 넣는다(B1 · E46~E52).
  - **곧장 발행**(`shelve_bundle`): 리눅스 풀(로컬 레인이 안 집는다)의 keep 잡을 store 로 끝내고
    tar 를 손으로 만들어 `publish_bundle` 로 낸다. 헤더 · 경로 · 시계처럼 HTTP 만 보는 시험이 잡
    하나에 1초씩 쓰지 않게 한다. 원격 경로를 흉내 내는 데는 쓰지 않는다(그건 `RemoteWorker`).
- 기다리기는 `GET /api/shelf/N`(시계를 안 돌린다) 50 ms 간격이다. sleep 으로 맞추지 않는다.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import tarfile
import time
from pathlib import Path
from typing import Any

from remote_ci_monitor.config import parse_preset
from remote_ci_monitor.core import artifacts as art
from remote_ci_monitor.core.model import QUEUED, SUCCEEDED
from remote_ci_monitor.shelf_copier import ShelfCopier
from test_server import make_tar, sh
from test_server_m5e import ARTIFACT_CONFIG
from test_worker_api import T0, WorkerServer

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
MIB = 1024 * 1024
DAY = 86400.0

#: `ARTIFACT_CONFIG` 를 바탕으로 — 픽스처 tar 를 트리에 싣고, CI 디스크 바닥을 끄고, 청소기가 시험
#: 사이에 끼지 않게 한다(청소 회차를 보는 시험만 1초로 바꾼다).
SHELF_CFG: dict[str, Any] = {
    **ARTIFACT_CONFIG,
    "max_artifact_bytes": MIB,
    "max_artifact_files": 8,
    "artifact_storage_max_bytes": 256 * MIB,
    "max_snapshot_bytes": 64 * MIB,
    "shelf_max_bytes": 64 * MIB,
    "min_free_bytes": 0,
    "retention_sweep_interval_seconds": 3600,
}

KEEPGOLD = (
    "set -e; export COPYFILE_DISABLE=1; "
    "mkdir -p out test/feature/home/goldens; "
    "cp fixtures/home.png out/a.png; "
    "cp fixtures/home.png test/feature/home/goldens/home.png; "
    "tar -czf out/goldens.tar.gz test/feature/home/goldens/home.png; "
    "printf '{\"changed\":1}' > out/manifest.json; echo done > out/run.log"
)
COPYFIX = "mkdir -p out && cp -R fixtures/. out/"

SHELF_PRESETS = [
    sh("keepgold", KEEPGOLD, keep=True, artifacts=["out/*"]),
    sh("plaingold", KEEPGOLD, artifacts=["out/*"]),
    sh(
        "failkeep",
        KEEPGOLD + '; exit "${RCM_INPUT_CODE:-3}"',
        keep=True,
        artifacts=["out/*"],
        artifacts_on="failure",
        inputs=[{"name": "code", "type": "string", "default": "3"}],
    ),
    sh("noart", "echo nothing"),
    sh("linkeep", KEEPGOLD, keep=True, artifacts=["out/*"], pool="linux"),
    sh("copyfix", COPYFIX, keep=True, artifacts=["out/**"]),
    sh("bigkeep", COPYFIX, keep=True, artifacts=["out/**"]),
    sh("slowkeep", KEEPGOLD + "; sleep 30", keep=True, artifacts=["out/*"]),
    sh("lincopy", COPYFIX, keep=True, artifacts=["out/**"], pool="linux"),
]


def shelf_presets(extra: list[dict[str, Any]] | None = None) -> tuple[Any, ...]:
    return tuple(parse_preset(p) for p in [*SHELF_PRESETS, *(extra or [])])


def make_server(tmp_path: Path, *, workers: bool = True, **over: Any) -> WorkerServer:
    """보관함 시험용 서버. `workers=True` 면 `App.start` 가 copier 와 로컬 레인을 띄운다."""
    cfg = {**SHELF_CFG, **over}
    srv = WorkerServer(tmp_path, workers=False, **cfg)
    srv.cfg.presets = shelf_presets()
    srv.tokens["carol"] = srv.store.add_token("carol-x", admin=False, now=T0)
    if workers:
        srv.app.start()
    return srv


def reopen(
    srv: WorkerServer, *, start: bool = True, close: bool = True, **over: Any
) -> WorkerServer:
    """같은 data_dir · 시계 · 토큰으로 서버를 다시 띄운다(재시작). 설정은 `over` 만 바뀐다."""
    import threading

    from remote_ci_monitor.server import App
    from remote_ci_monitor.server import make_server as make_http
    from remote_ci_monitor.store import Store

    if close:
        srv.close()
    for k, v in over.items():
        setattr(srv.cfg.server, k, v)
    new = WorkerServer.__new__(WorkerServer)
    new.cfg = srv.cfg
    new.clock = srv.clock
    new.store = Store(srv.cfg.data_dir / "rcm.sqlite3")
    new.tokens = srv.tokens
    new.app = App(new.cfg, new.store, now_fn=new.clock)
    new.httpd = make_http(new.app, bind="127.0.0.1", port=0)
    new.port = new.httpd.server_address[1]
    new.thread = threading.Thread(
        target=new.httpd.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True
    )
    new.thread.start()
    if start:
        new.app.start()
    return new


# ── 잡 ───────────────────────────────────────────────────────────────────────

_TREE_SEQ = [0]


def fresh_tree() -> str:
    """시험마다 다른 트리 해시 — 합류가 우연히 성립하지 않게."""
    _TREE_SEQ[0] += 1
    return hashlib.sha256(f"tree-{_TREE_SEQ[0]}-{time.monotonic_ns()}".encode()).hexdigest()


def submit_body(preset: str, token: str = "alice", **over: Any) -> dict[str, Any]:
    tree = over.pop("tree_hash", None) or fresh_tree()
    branch = over.pop("branch", None)
    body: dict[str, Any] = {
        "preset": preset,
        "inputs": over.pop("inputs", {}),
        "source": {
            "mode": "tree",
            "repo": "org/app",
            "base_sha": "abc123f",
            "dirty": True,
            "tree_hash": tree,
            "bytes": 1,
        },
        "requester_label": over.pop("label", f"{token}@host"),
    }
    if branch is not None:
        body["source"]["branch"] = branch
    body.update(over)
    return body


def submit(srv: WorkerServer, preset: str, token: str = "alice", **over: Any):
    return srv.req("POST", "/jobs", token=token, json_body=submit_body(preset, token, **over))


def upload(srv: WorkerServer, jid: int, files: dict[str, bytes], token: str = "alice") -> None:
    status, body = srv.req("PUT", f"/jobs/{jid}/tree", token=token, body=make_tar(files))
    assert status == 200 and body["state"] == QUEUED, (status, body)


def fixtures(extra: dict[str, bytes] | None = None) -> dict[str, bytes]:
    return {"fixtures/home.png": PNG_1PX, **(extra or {})}


def run_local(
    srv: WorkerServer,
    preset: str = "keepgold",
    files: dict[str, bytes] | None = None,
    *,
    token: str = "alice",
    wait: bool = True,
    **over: Any,
) -> int:
    """진짜 로컬 레인으로 잡 하나 — 제출 · 업로드 · 끝날 때까지."""
    status, body = submit(srv, preset, token, **over)
    assert status == 201, (status, body)
    jid = int(body["job_id"])
    upload(srv, jid, files if files is not None else fixtures(), token)
    if wait:
        wait_job(srv, jid)
    return jid


def wait_job(srv: WorkerServer, jid: int, timeout: float = 30.0) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = srv.store.get_job(jid)
        if job is not None and job.is_terminal:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {jid} did not finish: {srv.store.get_job(jid)}")


def shelf_doc(srv: WorkerServer, jid: int) -> tuple[int, Any]:
    return srv.req("GET", f"/api/shelf/{jid}")


def wait_shelf(srv: WorkerServer, jid: int, states: Any = ("active",), timeout: float = 10.0):
    """`GET /api/shelf/N` 이 그 상태가 될 때까지(시계를 안 돌리는 경로)."""
    want = (states,) if isinstance(states, str) else tuple(states)
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        status, body = shelf_doc(srv, jid)
        last = (status, body)
        if status == 200 and isinstance(body, dict) and body.get("state") in want:
            return body
        time.sleep(0.05)
    raise AssertionError(f"shelf #{jid} never reached {want}: {last}")


def wait_until(pred: Any, timeout: float = 10.0, what: str = "condition") -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        got = pred()
        if got:
            return got
        time.sleep(0.05)
    raise AssertionError(f"{what} never held")


def drain_copier(srv: WorkerServer) -> None:
    """서버가 띄우는 것과 같은 copier 를 이 스레드에서 pending 이 없어질 때까지 돌린다.

    보관 행이 `pending` 에 머문 상태를 보는 시험은 copier 스레드 없이 서버를 띄우고(`workers=False`
    거나 `ShelfCopier.start` 를 막는다) 이것으로 보관을 끝낸다. copier 스레드를 띄운 채 문(Gate)으로
    붙잡으면 문을 달기 전에 들어간 claim 이 막 생긴 행을 집어 갈 수 있다.
    """
    app = srv.app
    ShelfCopier(
        app.store,
        app.config,
        now_fn=app.now_fn,
        log=app.log,
        on_change=app._on_shelf_change,
        on_purged=app._shelf_purged,
    ).drain()


# ── 손으로 만든 묶음 (HTTP 만 보는 시험) ─────────────────────────────────────


def tar_bytes(members: list[tarfile.TarInfo | tuple[str, bytes]], *, fmt: int = tarfile.GNU_FORMAT):
    """무압축 tar. `(이름, 바이트)` 는 일반 파일, `TarInfo` 는 그대로(링크 · 장치 · 디렉터리)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=fmt) as tf:
        for m in members:
            if isinstance(m, tarfile.TarInfo):
                tf.addfile(m)
            else:
                name, data = m
                info = tarfile.TarInfo(name)
                info.size = len(data)
                info.mode = 0o644
                tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def targz_bytes(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.GNU_FORMAT) as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def bundle_files_of(payload: bytes) -> tuple[Any, ...]:
    out = []
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as tf:
        for m in tf.getmembers():
            if m.isreg() and not m.issparse():
                data = tf.extractfile(m).read()  # type: ignore[union-attr]
                out.append(
                    art.BundleFile(
                        path=m.name,
                        size=len(data),
                        sha256=hashlib.sha256(data).hexdigest(),
                        mode=0o644,
                    )
                )
    return tuple(out)


def publish_payload(srv: WorkerServer, jid: int, payload: bytes) -> str:
    """bundle.tar 를 놓고 `publish_bundle` 로 낸다(keep 잡이면 그 트랜잭션이 보관 행을 세운다)."""
    import remote_ci_monitor.collect as collect

    d = srv.cfg.data_dir / "artifacts" / str(jid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "bundle.tar").write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    files = bundle_files_of(payload)
    (d / "manifest.json").write_text(json.dumps({"bundle_sha256": digest, "files": []}))
    now = srv.clock.now
    srv.store.start_collect(
        jid, art.ArtifactPolicy(globs=("out/**",), max_bytes=MIB, max_files=8), now
    )
    srv.store.publish_bundle(
        jid,
        collect.CollectResult(
            state=art.READY,
            files=files,
            total_bytes=sum(f.size for f in files),
            bundle_bytes=len(payload),
            skipped_count=0,
            bundle_sha256=digest,
            bundle_path=d / "bundle.tar",
        ),
        now=now,
        expires_at=art.expires_at(now, srv.cfg.server.artifact_retention_hours),
    )
    return digest


def finished_linux_job(
    srv: WorkerServer, preset: str = "lincopy", token: str = "alice", **over: Any
) -> int:
    """리눅스 풀 keep 잡을 store 로 끝낸다(로컬 레인이 안 집는다)."""
    status, body = submit(srv, preset, token, **over)
    assert status == 201, (status, body)
    jid = int(body["job_id"])
    upload(srv, jid, {"hello.txt": b"hi\n"}, token)
    claimed = srv.store.claim(1, srv.clock.now, pool="linux", worker_name="lin-01")
    assert claimed is not None and claimed.id == jid
    assert srv.store.finish(jid, SUCCEEDED, now=srv.clock.now, exit_code=0)
    return jid


def shelve_bundle(
    srv: WorkerServer,
    members: list[Any] | dict[str, bytes],
    *,
    states: Any = ("active",),
    **over: Any,
) -> int:
    """손으로 만든 묶음을 보관함까지 — (잡 번호)."""
    if isinstance(members, dict):
        members = list(members.items())
    jid = finished_linux_job(srv, **over)
    publish_payload(srv, jid, tar_bytes(members))
    srv.app._on_job_change(jid)  # 진짜로 잡이 끝났을 때 copier 를 깨우는 자리와 같다
    if states:
        wait_shelf(srv, jid, states)
    return jid


def item_dir(srv: WorkerServer, jid: int) -> Path:
    return srv.cfg.shelf_dir / str(jid)


def manifest_of(srv: WorkerServer, jid: int) -> dict[str, Any]:
    return json.loads((item_dir(srv, jid) / "manifest.json").read_text())


def advance_days(srv: WorkerServer, days: float) -> None:
    srv.clock.advance(days * DAY)


def last_used(srv: WorkerServer, jid: int) -> float | None:
    item = srv.store.shelf_item(jid)
    assert item is not None
    return item.last_used_at
