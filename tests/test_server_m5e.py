"""서버(M5e) — 산출물 라우트 표를 전수로 잠근다: 자격 · 상태별 상태 코드 · 스트리밍 헤더 ·
ack 의 Bearer 전용 · 워커 업로드와 복구 GET · 전송 슬롯 · 잡 JSON 의 추가 키와 이벤트.

명세는 docs/m5e-workplan.md **§6 표(계약)** · §7(삭제 규칙) · §10(추가만 하는 JSON) ·
§18(잠근 API 표면). **구현 전이라 빨간 것이 정상이다.**

- `test_worker_api.WorkerServer`(in-process HTTP + **주입 시계** + client/admin/worker 토큰)를
  그대로 쓴다. 시간은 `srv.clock.advance()` 로만 민다 — sleep 은 없다.
- 프리셋 `goldens` 만 `artifacts` 글롭을 선언한다. `ok` 는 선언하지 않는다 → `disabled`.
  설정이 아직 그 키를 모르면 글롭 없이 파싱해 둔다(그 키 자체는
  `test_preset_artifacts_globs_are_a_config_key` 가 따로 잠근다) — 파일 전체가
  프리셋 파싱 하나로 죽지 않게.
- 번들은 `store.publish_bundle` 로 직접 발행하고 파일을 `<data_dir>/artifacts/<id>/` 에 놓는다.
  워커 수집기는 이 파일의 관심사가 아니다(역할 A).
"""

from __future__ import annotations

import base64
import hashlib
import json
import socket
import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from remote_ci_monitor.config import ConfigError, parse_preset
from remote_ci_monitor.core.model import LOST, SUCCEEDED
from remote_ci_monitor.core.status import iso
from test_server import TAR, TREE_HASH, sh
from test_worker_api import T0, WorkerServer

HOUR = timedelta(hours=1)
TTL_HOURS = 24
GOLDEN_GLOBS = ["test/**/goldens/*.png", "test/failures/*.png"]
TAR_TYPE = {"Content-Type": "application/x-tar"}
STRANGER = "carol-x"

#: 잡당 상한을 작게 잡아 admission 경계를 눈으로 볼 수 있게 한다.
#: 아카이브 허용치 = 1000 + 4×1024 + 10240 = 15336 (§8 회계). 파일당 1024 는 헤더와 데이터 끝의
#: 512 정렬 패딩이고, 10240 은 `tarfile` 이 닫을 때 채우는 레코드다.
MAX_BYTES = 1000
MAX_FILES = 4
ALLOWANCE = MAX_BYTES + MAX_FILES * 1024 + 10240

ARTIFACT_CONFIG: dict[str, Any] = {
    "artifact_retention_hours": TTL_HOURS,
    "max_artifact_bytes": MAX_BYTES,
    "max_artifact_files": MAX_FILES,
    "artifact_storage_max_bytes": 10 * 1024 * 1024,
    "artifact_timeout_seconds": 60,
    "artifact_cancel_timeout_seconds": 5,
    "artifact_transfer_timeout_seconds": 300,
    "max_concurrent_artifact_transfers": 2,
}


# ── 아직 없는 모듈 (§18) ──────────────────────────────────────────────────────


def artifacts_core() -> Any:
    """`remote_ci_monitor.core.artifacts`(§18). 구현 전에는 여기서 빨개진다."""
    import remote_ci_monitor.core.artifacts as mod

    return mod


def collect_result(**kw: Any) -> Any:
    import remote_ci_monitor.collect as mod

    return mod.CollectResult(**kw)


def bundle_file(path: str, data: bytes, mode: int = 0o644) -> Any:
    return artifacts_core().BundleFile(
        path=path, size=len(data), sha256=hashlib.sha256(data).hexdigest(), mode=mode
    )


# ── 서버 ─────────────────────────────────────────────────────────────────────


def artifact_presets() -> tuple[Any, ...]:
    """`goldens`(글롭 있음) · `ok`(글롭 없음 → disabled) · `slow`."""
    raw = [
        {**sh("goldens", "echo goldens"), "artifacts": list(GOLDEN_GLOBS)},
        sh("ok", "echo ok"),
        sh("slow", "echo slow"),
    ]
    out = []
    for entry in raw:
        try:
            out.append(parse_preset(entry))
        except ConfigError:
            # 설정이 아직 `artifacts` 를 모른다 — 글롭만 떼고 라우트 검사는 계속한다
            out.append(parse_preset({k: v for k, v in entry.items() if k != "artifacts"}))
    return tuple(out)


@pytest.fixture
def srv(tmp_path):
    s = WorkerServer(tmp_path, **ARTIFACT_CONFIG)
    s.cfg.presets = artifact_presets()
    s.tokens["carol"] = s.store.add_token(STRANGER, admin=False, now=T0)
    yield s
    s.close()


# ── 도우미 ───────────────────────────────────────────────────────────────────


def submit(srv: WorkerServer, token: str, *, preset: str = "goldens", tree: str = TREE_HASH):
    """`join` 을 안 보낸다 — 같은 프리셋·트리면 합류가 성립한다(`join_or_bump` 경로)."""
    body = {
        "preset": preset,
        "inputs": {},
        "source": {
            "mode": "tree",
            "repo": "org/app",
            "base_sha": "abc123f",
            "dirty": True,
            "tree_hash": tree,
            "bytes": len(TAR),
        },
        "requester_label": f"{token}@host",
    }
    return srv.req("POST", "/jobs", token=token, json_body=body)


def new_job(srv: WorkerServer, token: str = "alice", **kw: Any) -> int:
    status, body = submit(srv, token, **kw)
    assert status == 201, body
    return int(body["job_id"])


def queued(srv: WorkerServer, token: str = "alice", **kw: Any) -> int:
    jid = new_job(srv, token, **kw)
    status, body = srv.req("PUT", f"/jobs/{jid}/tree", token=token, body=TAR)
    assert status == 200, body
    return jid


def run_to(srv: WorkerServer, jid: int, state: str = SUCCEEDED, *, lane: int = 1) -> None:
    """워커 없이 잡을 끝낸다 — 그 잡을 claim 한 뒤 종료 상태로."""
    claimed = srv.store.claim(lane, srv.clock.now)
    assert claimed is not None and claimed.id == jid, (claimed and claimed.id, jid)
    assert srv.store.finish(
        jid, state, now=srv.clock.now, exit_code=0 if state == SUCCEEDED else None
    )


def bundle_dir(srv: WorkerServer, jid: int) -> Path:
    return srv.cfg.data_dir / "artifacts" / str(jid)


def install(srv: WorkerServer, jid: int, payload: bytes) -> str:
    d = bundle_dir(srv, jid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "bundle.tar").write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    (d / "manifest.json").write_text(json.dumps({"bundle_sha256": digest, "files": []}))
    return digest


def publish(
    srv: WorkerServer,
    jid: int,
    *,
    payload: bytes = b"golden tar",
    files: tuple[Any, ...] | None = None,
    ready_at: Any = None,
    ttl_hours: int = TTL_HOURS,
) -> str:
    core = artifacts_core()
    ready_at = ready_at or srv.clock.now
    digest = install(srv, jid, payload)
    entries = files if files is not None else (bundle_file("test/goldens/a.png", b"png"),)
    srv.store.start_collect(jid, policy(), ready_at)  # 진짜 순서 — 행을 열고 나서 발행한다
    result = collect_result(
        state=core.READY,
        files=entries,
        total_bytes=sum(f.size for f in entries),
        bundle_bytes=len(payload),
        skipped_count=0,
        bundle_sha256=digest,
        bundle_path=bundle_dir(srv, jid) / "bundle.tar",
    )
    srv.store.publish_bundle(
        jid, result, now=ready_at, expires_at=core.expires_at(ready_at, ttl_hours)
    )
    return digest


def ready_job(srv: WorkerServer, *, token: str = "alice", **kw: Any) -> tuple[int, str]:
    """제출 → 업로드 → 성공 → 번들 발행. (잡 번호, bundle_sha256)."""
    jid = queued(srv, token)
    run_to(srv, jid)
    return jid, publish(srv, jid, **kw)


def policy() -> Any:
    core = artifacts_core()
    return core.ArtifactPolicy(globs=tuple(GOLDEN_GLOBS), max_bytes=MAX_BYTES, max_files=MAX_FILES)


def set_bundle_state(srv: WorkerServer, jid: int, state: str, reason: str | None = None) -> None:
    """`ready` 가 아닌 상태를 손으로 놓는다 — 수집기 없이 라우트만 본다."""
    core = artifacts_core()
    srv.store.start_collect(jid, policy(), srv.clock.now)
    if state == core.COLLECTING:
        return
    if state in (core.PURGED, core.EXPIRED):
        raise AssertionError("purged·expired 는 발행 뒤 mark_bundles_purged 로 만든다")
    srv.store.set_bundle_failed(jid, state, reason or "collect_failed", None, srv.clock.now)


def raw_bundle_state(srv: WorkerServer, jid: int, state: str) -> None:
    """워커가 만드는 `uploading` 처럼 API 로는 못 놓는 상태를 DB 에 직접 둔다."""
    conn = sqlite3.connect(srv.store.path)
    try:
        conn.execute("UPDATE job_artifacts SET state=? WHERE job_id=?", (state, jid))
        conn.commit()
    finally:
        conn.close()


def artifacts_of(srv: WorkerServer, jid: int, token: str | None = None) -> dict[str, Any]:
    status, body = srv.req("GET", f"/jobs/{jid}/artifacts", token=token or "alice")
    assert status == 200, (status, body)
    return body


def archive(srv: WorkerServer, jid: int, token: str = "alice", method: str = "GET"):
    return srv.req(method, f"/jobs/{jid}/artifacts/archive", token=token, raw=True)


def ack(srv: WorkerServer, jid: int, sha: Any, token: str = "alice", **kw: Any):
    return srv.req(
        "POST", f"/jobs/{jid}/artifacts/ack", token=token, json_body={"bundle_sha256": sha}, **kw
    )


def basic_header(name: str, secret: str) -> dict[str, str]:
    raw = base64.b64encode(f"{name}:{secret}".encode()).decode()
    return {"Authorization": f"Basic {raw}"}


def worker_owns(srv: WorkerServer, worker: str = "build-02", *, preset: str = "goldens") -> int:
    """워커가 claim 한 잡 하나."""
    srv.registered(worker)
    jid = queued(srv)
    assert srv.claimed(worker) == jid
    return jid


# ══ 자격 (§6) ════════════════════════════════════════════════════════════════


def test_requester_joiner_and_admin_all_read_the_status_and_the_archive(srv):
    jid = queued(srv, "alice")
    status, body = submit(srv, "bob")  # 같은 프리셋·트리 → 합류
    assert status == 200 and body["joined"] is True and body["job_id"] == jid
    run_to(srv, jid)
    sha = publish(srv, jid, payload=b"shared tar")
    for token in ("alice", "bob", "admin"):
        doc = artifacts_of(srv, jid, token)
        assert doc["state"] == artifacts_core().READY, token
        assert doc["bundle_sha256"] == sha, token
        code, headers, payload = archive(srv, jid, token)
        assert code == 200, (token, code)
        assert payload == b"shared tar", token


def test_a_worker_token_is_refused_on_the_artifact_reads(srv):
    """`can_read_log` 은 토큰 종류를 안 본다 — 산출물 라우트는 워커를 명시적으로 거부한다(§6)."""
    jid, sha = ready_job(srv)
    srv.registered("build-02")
    assert srv.req("GET", f"/jobs/{jid}/artifacts", token="build-02")[0] == 403
    assert archive(srv, jid, "build-02")[0] == 403
    assert ack(srv, jid, sha, token="build-02")[0] == 403


def test_a_stranger_is_403_and_no_token_is_401(srv):
    jid, sha = ready_job(srv)
    assert srv.req("GET", f"/jobs/{jid}/artifacts", token="carol")[0] == 403
    assert archive(srv, jid, "carol")[0] == 403
    assert ack(srv, jid, sha, token="carol")[0] == 403
    assert srv.req("GET", f"/jobs/{jid}/artifacts")[0] == 401
    assert srv.req("GET", f"/jobs/{jid}/artifacts/archive", raw=True)[0] == 401
    assert srv.req("POST", f"/jobs/{jid}/artifacts/ack", json_body={"bundle_sha256": sha})[0] == 401


def test_protected_reads_require_a_token_even_when_read_auth_is_none(srv):
    """산출물은 공개 읽기가 아니다 — `read_auth = none` 이어도 토큰을 요구한다(§6)."""
    assert srv.cfg.server.read_auth == "none"
    jid, _sha = ready_job(srv)
    assert srv.req("GET", "/api/status")[0] == 200  # 다른 읽기는 열려 있다
    assert srv.req("GET", f"/jobs/{jid}")[0] == 200
    assert srv.req("GET", f"/jobs/{jid}/artifacts")[0] == 401
    assert srv.req("GET", f"/jobs/{jid}/artifacts/archive", raw=True)[0] == 401
    assert srv.req("HEAD", f"/jobs/{jid}/artifacts/archive", raw=True)[0] == 401


def test_the_status_route_is_404_for_a_job_that_does_not_exist(srv):
    assert srv.req("GET", "/jobs/9999/artifacts", token="alice")[0] == 404
    assert srv.req("GET", "/jobs/9999/artifacts/archive", token="alice", raw=True)[0] == 404


def test_basic_credentials_may_read_but_never_ack(tmp_path):
    """브라우저가 Basic 을 자동으로 붙인다 — ack 를 Bearer 로 묶지 않으면 내부망 CSRF 로 남의
    산출물이 지워진다(§6)."""
    s = WorkerServer(tmp_path, read_auth="basic", **ARTIFACT_CONFIG)
    try:
        s.cfg.presets = artifact_presets()
        jid, sha = ready_job(s)
        header = basic_header("alice-laptop", s.tokens["alice"])
        assert s.req("GET", f"/jobs/{jid}/artifacts", headers=header)[0] == 200
        assert s.req("GET", f"/jobs/{jid}/artifacts/archive", headers=header, raw=True)[0] == 200
        status, body = s.req(
            "POST",
            f"/jobs/{jid}/artifacts/ack",
            headers=header,
            json_body={"bundle_sha256": sha},
        )
        assert status == 401, body
        assert s.store.get_bundle(jid)["acked_at"] is None
    finally:
        s.close()


# ══ GET /jobs/{id}/artifacts — 상태별 문서 (§6) ══════════════════════════════


def test_a_preset_without_globs_reads_disabled(srv):
    jid = queued(srv, preset="ok")
    assert artifacts_of(srv, jid)["state"] == artifacts_core().DISABLED


def test_an_unfinished_job_reads_pending(srv):
    jid = queued(srv)
    doc = artifacts_of(srv, jid)
    assert doc["state"] == artifacts_core().PENDING
    assert doc["reason_code"] is None


def test_a_finished_job_that_reported_nothing_reads_unknown_not_empty(srv):
    """보고 필드가 통째로 없으면 `unknown` 이다 — `empty` 로 소급하지 않는다(§3 · §5)."""
    jid = queued(srv)
    run_to(srv, jid)
    assert srv.store.get_bundle(jid) is None
    doc = artifacts_of(srv, jid)
    assert doc["state"] == artifacts_core().UNKNOWN


def test_non_ready_states_carry_only_the_state_and_reason_code(srv):
    """`ready` 가 아닌 상태는 매니페스트를 주지 않는다(§6). 경로는 어디에도 없다."""
    core = artifacts_core()
    cases = [
        (core.EMPTY, "no_match"),
        (core.DROPPED, "over_bytes"),
        (core.FAILED, "collect_failed"),
        (core.SKIPPED, "not_run"),
        (core.UNAVAILABLE, "interrupted"),
    ]
    for i, (state, reason) in enumerate(cases):
        jid = queued(srv, tree=f"{i:02d}" * 32)
        run_to(srv, jid)
        set_bundle_state(srv, jid, state, reason)
        doc = artifacts_of(srv, jid)
        assert doc["state"] == state
        assert doc["reason_code"] == reason
        assert not doc.get("files"), doc
        assert not (doc.get("manifest") or {}).get("files"), doc
        assert not doc.get("detail"), doc


def test_the_ready_document_carries_the_manifest_and_detail(srv):
    files = (
        bundle_file("test/goldens/a.png", b"aaaa"),
        bundle_file("test/failures/b.png", b"bb", mode=0o755),
    )
    jid, sha = ready_job(srv, files=files, payload=b"tar of two")
    doc = artifacts_of(srv, jid)
    core = artifacts_core()
    assert doc["state"] == core.READY
    assert doc["bundle_sha256"] == sha
    assert doc["file_count"] == 2 and doc["total_bytes"] == 6
    assert doc["bundle_bytes"] == len(b"tar of two")
    assert doc["skipped_count"] == 0
    assert doc["ready_at"] == iso(srv.clock.now)
    assert doc["expires_at"] == iso(srv.clock.now + TTL_HOURS * HOUR)
    assert "detail" in doc  # 경로가 들어갈 수 있는 자리 — 보호 라우트에만 있다(§6)
    listed = doc.get("files") or (doc.get("manifest") or {}).get("files")
    assert listed is not None, doc
    assert [f["path"] for f in listed] == ["test/goldens/a.png", "test/failures/b.png"]
    assert [f["sha256"] for f in listed] == [f.sha256 for f in files]
    assert [f["size"] for f in listed] == [4, 2]
    assert [f["mode"] for f in listed] == [0o644, 0o755]


# ══ GET · HEAD /jobs/{id}/artifacts/archive — 상태 표 (§6) ═══════════════════


def test_archive_is_409_while_the_bundle_is_still_in_progress(srv):
    """끝난 잡을 먼저 만들고 `pending` 잡을 마지막에 만든다 — `claim` 은 가장 작은 queued 를
    집으므로 순서를 지켜야 원하는 잡이 끝난다."""
    core = artifacts_core()
    for i, state in enumerate((core.COLLECTING, core.UPLOADING)):
        jid = queued(srv, tree=f"a{i}" * 32)
        run_to(srv, jid)
        srv.store.start_collect(jid, policy(), srv.clock.now)
        if state == core.UPLOADING:
            raw_bundle_state(srv, jid, core.UPLOADING)
        assert srv.store.get_bundle(jid)["state"] == state
        assert archive(srv, jid)[0] == 409, state
    pending = queued(srv)
    assert artifacts_of(srv, pending)["state"] == core.PENDING
    assert archive(srv, pending)[0] == 409


def test_archive_is_404_when_there_is_nothing_to_download(srv):
    core = artifacts_core()
    disabled = queued(srv, preset="ok")
    run_to(srv, disabled)
    assert archive(srv, disabled)[0] == 404
    unknown = queued(srv, tree="11" * 32)
    run_to(srv, unknown)
    assert archive(srv, unknown)[0] == 404
    for i, (state, reason) in enumerate(((core.EMPTY, "no_match"), (core.SKIPPED, "not_run"))):
        jid = queued(srv, tree=f"b{i}" * 32)
        run_to(srv, jid)
        set_bundle_state(srv, jid, state, reason)
        assert archive(srv, jid)[0] == 404, state


def test_archive_is_410_after_purge_expiry_or_past_the_expiry_moment(srv):
    core = artifacts_core()
    purged, _ = ready_job(srv)
    srv.store.mark_bundles_purged([purged], core.PURGED, srv.clock.now)
    assert archive(srv, purged)[0] == 410
    expired = queued(srv, tree="22" * 32)
    run_to(srv, expired)
    publish(srv, expired)
    srv.store.mark_bundles_purged([expired], core.EXPIRED, srv.clock.now)
    assert archive(srv, expired)[0] == 410
    # 만료 시각이 지나면 청소기가 아직 안 왔어도 새 다운로드는 410 이다(§6)
    live = queued(srv, tree="33" * 32)
    run_to(srv, live)
    publish(srv, live)
    assert archive(srv, live)[0] == 200
    srv.clock.advance(TTL_HOURS * 3600 + 1)
    assert archive(srv, live)[0] == 410
    assert srv.store.get_bundle(live)["state"] == core.READY  # 파일은 아직 있다


def test_archive_is_503_when_the_metadata_is_there_but_the_file_is_not(srv):
    core = artifacts_core()
    jid, _sha = ready_job(srv)
    (bundle_dir(srv, jid) / "bundle.tar").unlink()
    srv.store.reconcile_bundles_on_start(srv.clock.now)
    assert srv.store.get_bundle(jid)["state"] == core.UNAVAILABLE
    status, headers, _ = archive(srv, jid)
    assert status == 503
    assert artifacts_of(srv, jid)["state"] == core.UNAVAILABLE


def test_archive_streams_the_tar_with_no_store_and_attachment_headers(srv):
    payload = b"golden bundle bytes"
    jid, _sha = ready_job(srv, payload=payload)
    status, headers, body = archive(srv, jid)
    assert status == 200
    assert headers["Content-Type"] == "application/x-tar"
    assert headers["Content-Length"] == str(len(payload))
    assert headers["Cache-Control"] == "no-store"
    assert headers["Content-Disposition"].startswith("attachment")
    assert body == payload


def test_head_archive_has_the_same_status_and_headers_with_no_body_or_side_effect(srv):
    core = artifacts_core()
    payload = b"head me"
    jid, sha = ready_job(srv, payload=payload)
    before = srv.store.get_bundle(jid)
    status, headers, body = archive(srv, jid, method="HEAD")
    assert status == 200 and body == b""
    assert headers["Content-Type"] == "application/x-tar"
    assert headers["Content-Length"] == str(len(payload))
    assert headers["Cache-Control"] == "no-store"
    after = srv.store.get_bundle(jid)
    assert after["state"] == core.READY == before["state"]
    assert after["acked_at"] is None and after["purged_at"] is None
    assert after["expires_at"] == before["expires_at"]  # 읽어도 연장되지 않는다(§8)
    assert (bundle_dir(srv, jid) / "bundle.tar").read_bytes() == payload
    # 없는 것에도 같은 상태 코드를 준다
    srv.store.mark_bundles_purged([jid], core.PURGED, srv.clock.now)
    assert archive(srv, jid, method="HEAD")[0] == 410
    assert srv.store.get_bundle(jid)["bundle_sha256"] == sha


# ══ 전송 슬롯 (§6) ═══════════════════════════════════════════════════════════


def test_the_transfer_slot_is_taken_without_blocking(tmp_path):
    """전송 슬롯은 **절대 기다리지 않는다.** 일반 슬롯을 쥔 채 기다리면 heartbeat·cancel·status
    가 굶는다(§6). 헤더까지 받으면 첫 전송이 슬롯을 쥔 것이 확정이다 — sleep 이 필요 없다."""
    s = WorkerServer(tmp_path, **{**ARTIFACT_CONFIG, "max_concurrent_artifact_transfers": 1})
    held: socket.socket | None = None
    try:
        s.cfg.presets = artifact_presets()
        payload = b"x" * (4 * 1024 * 1024)  # 소켓 버퍼보다 크다 — 안 읽으면 서버가 멈춰 선다
        jid, _sha = ready_job(s, payload=payload)
        held = socket.create_connection(("127.0.0.1", s.port), timeout=15)
        held.sendall(
            f"GET /jobs/{jid}/artifacts/archive HTTP/1.1\r\nHost: x\r\n"
            f"Authorization: Bearer {s.tokens['alice']}\r\n\r\n".encode()
        )
        headers = b""
        while b"\r\n\r\n" not in headers:
            chunk = held.recv(4096)
            assert chunk, "첫 전송이 헤더도 못 보냈다"
            headers += chunk
        assert headers.split(b"\r\n", 1)[0].split(b" ")[1] == b"200", headers[:80]
        # 슬롯이 없다 → 기다리지 않고 즉시 503 + Retry-After
        status, resp_headers, _ = archive(s, jid)
        assert status == 503, status
        assert resp_headers.get("Retry-After")
        # 일반 요청은 굶지 않는다
        assert s.req("GET", "/api/health")[0] == 200
        assert s.req("GET", f"/jobs/{jid}/artifacts", token="alice")[0] == 200
    finally:
        if held is not None:
            held.close()
        s.close()


# ══ POST /jobs/{id}/artifacts/ack (§6 · §7) ══════════════════════════════════


def test_ack_of_an_unjoined_job_purges_the_bundle_immediately(srv):
    """`join_count == 0` 인 잡은 확인이 오면 즉시 삭제(결정 40)."""
    core = artifacts_core()
    jid, sha = ready_job(srv)
    status, body = ack(srv, jid, sha)
    assert status == 200, body
    assert body["purged"] is True
    assert srv.store.get_bundle(jid)["state"] == core.PURGED
    assert not (bundle_dir(srv, jid) / "bundle.tar").exists()
    assert archive(srv, jid)[0] == 410
    assert artifacts_of(srv, jid)["state"] == core.PURGED


def test_ack_of_a_joined_job_keeps_the_bundle_until_the_ttl(srv):
    """한 번이라도 합류가 있었으면 확인이 와도 TTL 까지 유지한다 — 둘 다 받아야 한다(결정 40)."""
    core = artifacts_core()
    jid = queued(srv, "alice")
    assert submit(srv, "bob")[1]["joined"] is True
    run_to(srv, jid)
    sha = publish(srv, jid, payload=b"two readers")
    status, body = ack(srv, jid, sha, token="alice")
    assert status == 200 and body["purged"] is False
    assert srv.store.get_bundle(jid)["state"] == core.READY
    code, _headers, payload = archive(srv, jid, "bob")
    assert code == 200 and payload == b"two readers"
    status, body = ack(srv, jid, sha, token="bob")  # 둘째 확인도 지우지 않는다
    assert status == 200 and body["purged"] is False
    assert (bundle_dir(srv, jid) / "bundle.tar").exists()


def test_an_admin_ack_is_a_no_op(srv):
    """요청자도 합류자도 아닌 admin 은 `acked_at` 을 쓰지 않고 `{"purged": false}` 를 준다(§7)."""
    core = artifacts_core()
    jid, sha = ready_job(srv)
    status, body = ack(srv, jid, sha, token="admin")
    assert status == 200 and body["purged"] is False
    assert srv.store.get_bundle(jid)["acked_at"] is None
    assert srv.store.get_bundle(jid)["state"] == core.READY
    assert archive(srv, jid)[0] == 200


def test_ack_with_a_wrong_hash_is_409_and_the_replay_of_the_same_hash_is_200(srv):
    core = artifacts_core()
    jid, sha = ready_job(srv)
    assert ack(srv, jid, "ff" * 32)[0] == 409
    assert srv.store.get_bundle(jid)["state"] == core.READY
    assert ack(srv, jid, sha)[0] == 200
    assert srv.store.get_bundle(jid)["state"] == core.PURGED
    status, body = ack(srv, jid, sha)  # 응답을 잃은 클라이언트의 재시도
    assert status == 200, body
    assert body["purged"] is False
    assert ack(srv, jid, "ab" * 32)[0] == 409  # 묘비와 다른 해시는 재생이 아니다


def test_ack_after_the_expiry_moment_is_410(srv):
    jid, sha = ready_job(srv)
    srv.clock.advance(TTL_HOURS * 3600 + 1)
    assert ack(srv, jid, sha)[0] == 410
    assert srv.store.get_bundle(jid)["acked_at"] is None


def test_ack_of_a_bundle_that_is_not_ready_is_409(srv):
    core = artifacts_core()
    jid = queued(srv)
    run_to(srv, jid)
    set_bundle_state(srv, jid, core.FAILED, "collect_failed")
    assert ack(srv, jid, "cd" * 32)[0] == 409


def test_ack_with_a_bad_body_is_400(srv):
    jid, sha = ready_job(srv)
    assert srv.req("POST", f"/jobs/{jid}/artifacts/ack", token="alice", json_body={})[0] == 400
    assert ack(srv, jid, 7)[0] == 400
    assert ack(srv, jid, "not-a-hash")[0] == 400
    assert (
        srv.req(
            "POST",
            f"/jobs/{jid}/artifacts/ack",
            token="alice",
            body=b"{not json",
            headers={"Content-Type": "application/json"},
        )[0]
        == 400
    )
    assert srv.store.get_bundle(jid)["bundle_sha256"] == sha


def test_ack_is_not_a_get(srv):
    jid, _sha = ready_job(srv)
    assert srv.req("GET", f"/jobs/{jid}/artifacts/ack", token="alice")[0] == 405


# ══ PUT /worker/jobs/{id}/artifacts (§6) ═════════════════════════════════════


def test_worker_upload_returns_201_with_a_verified_receipt(srv):
    core = artifacts_core()
    jid = worker_owns(srv)
    payload = b"y" * 512
    status, body = srv.req(
        "PUT", f"/worker/jobs/{jid}/artifacts", token="build-02", body=payload, headers=TAR_TYPE
    )
    assert status == 201, body
    assert body["bundle_sha256"] == hashlib.sha256(payload).hexdigest()
    assert body["job_id"] == jid
    assert srv.store.get_bundle(jid)["state"] in (core.READY, core.UPLOADING)


def test_uploading_the_same_bytes_again_is_idempotent_200(srv):
    jid = worker_owns(srv)
    payload = b"z" * 256
    first = srv.req(
        "PUT", f"/worker/jobs/{jid}/artifacts", token="build-02", body=payload, headers=TAR_TYPE
    )
    assert first[0] == 201, first
    second = srv.req(
        "PUT", f"/worker/jobs/{jid}/artifacts", token="build-02", body=payload, headers=TAR_TYPE
    )
    assert second[0] == 200, second
    assert second[1]["bundle_sha256"] == first[1]["bundle_sha256"]


def test_uploading_a_different_bundle_is_409(srv):
    """묶음은 불변이다 — 한 번 발행된 내용은 바뀌지 않는다(§3)."""
    jid = worker_owns(srv)
    assert (
        srv.req(
            "PUT",
            f"/worker/jobs/{jid}/artifacts",
            token="build-02",
            body=b"a" * 64,
            headers=TAR_TYPE,
        )[0]
        == 201
    )
    status, body = srv.req(
        "PUT", f"/worker/jobs/{jid}/artifacts", token="build-02", body=b"b" * 64, headers=TAR_TYPE
    )
    assert status == 409, body
    assert srv.store.get_bundle(jid)["bundle_sha256"] == hashlib.sha256(b"a" * 64).hexdigest()


def test_uploading_to_a_terminal_job_is_409(srv):
    """늦게 도착한 업로드는 409 — 죽은 잡을 산출물로 되살리지 않는다(§5)."""
    for i, state in enumerate((SUCCEEDED, LOST)):
        worker = "build-02"
        srv.registered(worker)
        jid = queued(srv, tree=f"c{i}" * 32)
        assert srv.claimed(worker) == jid
        rc = 0 if state == SUCCEEDED else None
        assert srv.store.finish(jid, state, now=srv.clock.now, exit_code=rc)
        status, body = srv.req(
            "PUT", f"/worker/jobs/{jid}/artifacts", token=worker, body=b"late", headers=TAR_TYPE
        )
        assert status == 409, (state, status, body)
        assert srv.store.get_job(jid).state == state


def test_uploading_without_content_length_is_411(srv):
    jid = worker_owns(srv)
    status, _body = srv.req(
        "PUT",
        f"/worker/jobs/{jid}/artifacts",
        token="build-02",
        body=b"chunky",
        headers={**TAR_TYPE, "Transfer-Encoding": "chunked"},
    )
    assert status == 411


def test_uploading_over_the_archive_allowance_is_413_but_the_boundary_fits(srv):
    """admission 은 `Content-Length`(아카이브 바이트)로 본다 — 원본 상한과 직접 비교하면
    경계 크기의 정상 묶음이 거절된다(§8 회계)."""
    edge = worker_owns(srv)
    assert (
        srv.req(
            "PUT",
            f"/worker/jobs/{edge}/artifacts",
            token="build-02",
            body=b"e" * MAX_BYTES,
            headers=TAR_TYPE,
        )[0]
        == 201
    )
    srv.registered("build-03")
    over = queued(srv, tree="dd" * 32)
    assert srv.claimed("build-03") == over
    status, body = srv.req(
        "PUT",
        f"/worker/jobs/{over}/artifacts",
        token="build-03",
        body=b"o" * (ALLOWANCE + 1),
        headers=TAR_TYPE,
    )
    assert status == 413, body
    assert srv.store.get_job(over).state == "running"  # 잡은 산출물 때문에 죽지 않는다


def test_uploading_with_the_wrong_content_type_is_415(srv):
    jid = worker_owns(srv)
    status, _body = srv.req(
        "PUT",
        f"/worker/jobs/{jid}/artifacts",
        token="build-02",
        body=b"not a tar",
        headers={"Content-Type": "application/json"},
    )
    assert status == 415


def test_another_workers_job_is_403_and_a_client_token_is_403(srv):
    jid = worker_owns(srv, "build-02")
    srv.registered("build-03")
    assert (
        srv.req(
            "PUT",
            f"/worker/jobs/{jid}/artifacts",
            token="build-03",
            body=b"mine?",
            headers=TAR_TYPE,
        )[0]
        == 403
    )
    assert (
        srv.req(
            "PUT", f"/worker/jobs/{jid}/artifacts", token="alice", body=b"mine?", headers=TAR_TYPE
        )[0]
        == 403
    )


# ══ GET /worker/jobs/{id}/artifacts — 복구 (§6) ══════════════════════════════


def test_the_worker_recovery_get_answers_for_a_terminal_job(srv):
    """`_owned_active`(`remote_workers.py:364`)를 쓰면 이 라우트가 존재하는 이유인 모호한
    질문을 바로 409 로 거절한다(§6)."""
    jid = worker_owns(srv)
    payload = b"receipt bytes"
    up = srv.req(
        "PUT", f"/worker/jobs/{jid}/artifacts", token="build-02", body=payload, headers=TAR_TYPE
    )
    assert up[0] == 201, up
    assert srv.store.finish(jid, SUCCEEDED, now=srv.clock.now, exit_code=0)
    status, body = srv.req("GET", f"/worker/jobs/{jid}/artifacts", token="build-02")
    assert status == 200, body
    assert body["bundle_sha256"] == hashlib.sha256(payload).hexdigest()
    assert body["job_state"] == SUCCEEDED


def test_the_worker_recovery_get_reports_a_missing_receipt_so_it_can_be_sent_again(srv):
    jid = worker_owns(srv)
    status, body = srv.req("GET", f"/worker/jobs/{jid}/artifacts", token="build-02")
    assert status == 200, body
    assert body["bundle_sha256"] is None
    assert body["job_state"] == "running"


def test_the_worker_recovery_get_is_403_for_another_workers_job(srv):
    jid = worker_owns(srv, "build-02")
    srv.registered("build-03")
    assert srv.req("GET", f"/worker/jobs/{jid}/artifacts", token="build-03")[0] == 403
    assert srv.req("GET", f"/worker/jobs/{jid}/artifacts", token="alice")[0] == 403


# ══ 잡 JSON · 상태 문서 · 이벤트 (§10) ═══════════════════════════════════════


def test_job_json_carries_the_additive_artifacts_object(srv):
    core = artifacts_core()
    jid = queued(srv)
    active = srv.req("GET", f"/jobs/{jid}", token="alice")[1]
    assert active["artifacts"]["state"] == core.PENDING
    assert active["state"] == "queued"  # 기존 키는 그대로다(스키마 v1)
    run_to(srv, jid)
    publish(srv, jid, payload=b"tar!!")
    done = srv.req("GET", f"/jobs/{jid}", token="alice")[1]
    a = done["artifacts"]
    assert set(a) >= {
        "state",
        "file_count",
        "total_bytes",
        "bundle_bytes",
        "skipped_count",
        "ready_at",
        "expires_at",
        "purged_at",
        "reason_code",
        "reason_args",
    }
    assert a["state"] == core.READY
    assert a["file_count"] == 1 and a["bundle_bytes"] == 5
    assert a["ready_at"] == iso(srv.clock.now)
    assert a["expires_at"] == iso(srv.clock.now + TTL_HOURS * HOUR)
    assert a["purged_at"] is None and a["reason_code"] is None


def test_unknown_counts_are_null_and_zero_only_means_a_completed_empty_collection(srv):
    """모르는 수는 `null`. `0` 은 「모았는데 없었다」일 때만(§10)."""
    core = artifacts_core()
    unknown = queued(srv, tree="55" * 32)
    run_to(srv, unknown)
    a = srv.req("GET", f"/jobs/{unknown}", token="alice")[1]["artifacts"]
    assert a["state"] == core.UNKNOWN
    assert a["file_count"] is None and a["total_bytes"] is None and a["bundle_bytes"] is None
    empty = queued(srv, tree="66" * 32)
    run_to(srv, empty)
    srv.store.start_collect(empty, policy(), srv.clock.now)
    srv.store.publish_bundle(
        empty,
        collect_result(state=core.EMPTY, files=(), total_bytes=0, bundle_bytes=0, skipped_count=0),
        now=srv.clock.now,
        expires_at=core.expires_at(srv.clock.now, TTL_HOURS),
    )
    a = srv.req("GET", f"/jobs/{empty}", token="alice")[1]["artifacts"]
    assert a["state"] == core.EMPTY
    assert a["file_count"] == 0 and a["total_bytes"] == 0


def test_status_document_rows_carry_the_artifacts_object_and_the_storage_block(srv):
    jid, _sha = ready_job(srv, payload=b"stored bytes")
    waiting = queued(srv, tree="77" * 32)
    doc = srv.req("GET", "/api/status")[1]
    assert doc["schema_version"] == 1
    pool = doc["pools"][0]
    recent = {r["id"]: r for r in pool["recent"]}
    assert recent[jid]["artifacts"]["state"] == artifacts_core().READY
    queue = {r["id"]: r for r in pool["queue"]}
    assert queue[waiting]["artifacts"]["state"] == artifacts_core().PENDING
    storage = doc["server"]["artifact_storage"]
    assert set(storage) >= {
        "stored_bytes",
        "reserved_bytes",
        "limit_bytes",
        "last_sweep_at",
        "error_code",
    }
    assert storage["stored_bytes"] == len(b"stored bytes")
    assert storage["reserved_bytes"] == 0
    assert storage["limit_bytes"] == ARTIFACT_CONFIG["artifact_storage_max_bytes"]


def test_no_file_path_ever_reaches_the_public_document_or_reason_args(srv):
    """파일 목록·경로·해시는 보호 라우트에서만. 공개 문서에는 집계만(§10)."""
    core = artifacts_core()
    secret = "test/goldens/secret-customer-name.png"
    jid = queued(srv)
    run_to(srv, jid)
    publish(srv, jid, files=(bundle_file(secret, b"png"),))
    for path in (f"/jobs/{jid}", "/api/status"):
        body = json.dumps(srv.req("GET", path, token="alice")[1])
        assert secret not in body, path
        assert "secret-customer-name" not in body, path
        assert str(srv.cfg.data_dir) not in body, path
    dropped = queued(srv, tree="88" * 32)
    run_to(srv, dropped)
    srv.store.start_collect(dropped, policy(), srv.clock.now)
    srv.store.set_bundle_failed(
        dropped, core.DROPPED, "path_conflict", {"limit": 4, "seen": 9}, srv.clock.now
    )
    a = srv.req("GET", f"/jobs/{dropped}", token="alice")[1]["artifacts"]
    assert a["reason_code"] == "path_conflict"
    assert all("/" not in str(v) for v in (a["reason_args"] or {}).values()), a["reason_args"]
    # 경로는 보호 라우트의 detail·매니페스트에만 있다
    doc = artifacts_of(srv, jid)
    assert secret in json.dumps(doc)


def test_the_new_event_kind_is_artifacts_changed_and_it_invalidates_the_status_cache(srv):
    """`_publish_job` 을 쓰면 종료 잡에 `job_finished` 가 다시 나간다(`server.py:367`) — 새
    종류를 쓴다. 캐시도 무효화해야 방금 지운 것이 상태 문서에 바로 보인다(§10)."""
    core = artifacts_core()
    jid, sha = ready_job(srv)
    before = srv.req("GET", "/api/status")[1]["pools"][0]["recent"]
    assert {r["id"]: r for r in before}[jid]["artifacts"]["state"] == core.READY
    sub = srv.app.bus.subscribe()
    try:
        assert ack(srv, jid, sha)[0] == 200
        seen = []
        while True:
            ev = sub.get(0.5)
            if ev is None:
                break
            seen.append(ev)
    finally:
        srv.app.bus.unsubscribe(sub)
    kinds = [e.kind for e in seen]
    assert "artifacts_changed" in kinds, kinds
    assert "job_finished" not in kinds, kinds  # 끝난 잡을 두 번 끝내지 않는다
    changed = next(e for e in seen if e.kind == "artifacts_changed")
    assert changed.data["job_id"] == jid
    assert changed.data.get("state") == core.PURGED
    body = json.dumps(changed.data)
    assert "test/goldens" not in body and str(srv.cfg.data_dir) not in body
    after = srv.req("GET", "/api/status")[1]["pools"][0]["recent"]
    assert {r["id"]: r for r in after}[jid]["artifacts"]["state"] == core.PURGED


def test_a_collection_never_delays_the_v1_time_fields(srv):
    """v1 시간 필드의 뜻을 바꾸지 않는다 — 원격 워커가 **실행 종료 시각**을 실어 보내고 서버는
    그것을 쓴다. 산출물 시간은 `artifacts.ready_at` 으로 따로 본다(§10)."""
    jid = worker_owns(srv)
    started = srv.clock.now
    finished_at = started + timedelta(seconds=30)
    srv.clock.advance(150)  # 수집·업로드가 끼어 서버 수신이 2분 늦었다
    status, body = srv.req(
        "POST",
        f"/worker/jobs/{jid}/finish",
        token="build-02",
        json_body={
            "outcome": "succeeded",
            "exit_code": 0,
            "finished_at": iso(finished_at),
            "artifacts": {"state": "empty"},
        },
    )
    assert status == 200, body
    view = srv.req("GET", f"/jobs/{jid}", token="alice")[1]
    assert view["started_at"] == iso(started)
    assert view["finished_at"] == iso(finished_at)
    assert view["job_seconds"] == 30  # 수집 때문에 밀리지 않는다
    assert view["artifacts"]["state"] == artifacts_core().EMPTY


def test_an_old_worker_that_reports_no_artifacts_field_is_unknown_not_empty(srv):
    """필드가 통째로 없으면 `unknown` 이다. `{"state":"empty"}` 와 반드시 구분된다(§5)."""
    jid = worker_owns(srv)
    status, body = srv.req(
        "POST",
        f"/worker/jobs/{jid}/finish",
        token="build-02",
        json_body={"outcome": "succeeded", "exit_code": 0},
    )
    assert status == 200, body
    assert srv.req("GET", f"/jobs/{jid}", token="alice")[1]["artifacts"]["state"] == (
        artifacts_core().UNKNOWN
    )


# ══ 설정 (§18) ═══════════════════════════════════════════════════════════════


def test_preset_artifacts_globs_are_a_config_key():
    """`[[presets]].artifacts` 는 설정 키다(§4) — 기본값은 `()` 고 없으면 기능이 꺼진다."""
    p = parse_preset({**sh("goldens", "echo x"), "artifacts": list(GOLDEN_GLOBS)})
    assert p.artifacts == tuple(GOLDEN_GLOBS)
    assert parse_preset(sh("plain", "echo x")).artifacts == ()


def test_the_new_server_keys_have_the_spec_defaults():
    from remote_ci_monitor.config import ServerConfig

    s = ServerConfig().server
    assert s.artifact_retention_hours == 24
    assert s.max_artifact_bytes == 1_073_741_824
    assert s.max_artifact_files == 10_000
    assert s.artifact_storage_max_bytes == 10_737_418_240
    assert s.artifact_timeout_seconds == 60
    assert s.artifact_cancel_timeout_seconds == 5
    assert s.artifact_transfer_timeout_seconds == 300
    assert s.max_concurrent_artifact_transfers == 2
