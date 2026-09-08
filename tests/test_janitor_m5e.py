"""보존 정리(M5e) — 번들 TTL sweep 과 **M3 와의 경계**.

명세는 docs/m5e-workplan.md §8 「청소 · M3 와의 경계」 · §6(만료를 가로지르는 전송) · §18.
**구현 전이라 빨간 것이 정상이다.**

이 파일이 지키는 한 문장: **번들은 자기 시계로만 지운다.** `retention_days_success` 는 0 이 될 수
있고(`config.py:594`) 0 은 「다음 sweep 에 바로」다(`core/retention.py:82`) — M3 청소에 번들을
얹으면 합류된 잡이 몇 분 만에 산출물을 잃는다(결정 40 위반).

시각은 고정 `NOW` 기준으로 `sweep_once(now)` 에 넘긴다(스레드 없음, sleep 없음). 전송이 만료를
가로지르는 시험만 진짜 소켓을 쓰고, 기다림 대신 **같은 연결의 다음 응답**을 배리어로 삼는다.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import pathlib
import shutil
import socket
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from remote_ci_monitor.core.model import FAILED, SUCCEEDED
from remote_ci_monitor.janitor import Janitor
from remote_ci_monitor.store import Store
from test_janitor import DAY, NOW, finished, make_janitor, new_job, touch_dirs
from test_worker import make_config

HOUR = timedelta(hours=1)
TTL_HOURS = 24

ARTIFACT_CONFIG: dict[str, Any] = {
    "artifact_retention_hours": TTL_HOURS,
    "max_artifact_bytes": 1_073_741_824,
    "max_artifact_files": 10_000,
    "artifact_storage_max_bytes": 10 * 1024 * 1024,
    "artifact_timeout_seconds": 60,
    "artifact_cancel_timeout_seconds": 5,
    "artifact_transfer_timeout_seconds": 300,
    "max_concurrent_artifact_transfers": 2,
}


# ── 아직 없는 모듈 (§18) ──────────────────────────────────────────────────────


def artifacts_core() -> Any:
    import remote_ci_monitor.core.artifacts as mod

    return mod


def collect_result(**kw: Any) -> Any:
    import remote_ci_monitor.collect as mod

    return mod.CollectResult(**kw)


def bundle_file(path: str, data: bytes) -> Any:
    return artifacts_core().BundleFile(
        path=path, size=len(data), sha256=hashlib.sha256(data).hexdigest(), mode=0o644
    )


def policy() -> Any:
    return artifacts_core().ArtifactPolicy(globs=("test/goldens/*.png",))


# ── 도우미 ───────────────────────────────────────────────────────────────────


@pytest.fixture
def env(tmp_path):
    cfg = make_config(tmp_path, retention_days_success=1, retention_days_failure=2)
    for key, value in ARTIFACT_CONFIG.items():
        setattr(cfg.server, key, value)
    store = Store(cfg.data_dir / "rcm.sqlite3")
    yield store, cfg
    store.close()


def bundle_dir(cfg: Any, job_id: int) -> Path:
    return cfg.data_dir / "artifacts" / str(job_id)


def install(cfg: Any, job_id: int, payload: bytes) -> str:
    d = bundle_dir(cfg, job_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "bundle.tar").write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    (d / "manifest.json").write_text(json.dumps({"bundle_sha256": digest, "files": []}))
    return digest


def publish(
    store: Store,
    cfg: Any,
    job_id: int,
    *,
    payload: bytes = b"golden tar",
    ready_at,
    ttl_hours: int = TTL_HOURS,
) -> str:
    core = artifacts_core()
    digest = install(cfg, job_id, payload)
    files = (bundle_file("test/goldens/a.png", b"png"),)
    store.start_collect(job_id, policy(), ready_at)  # 진짜 순서 — 행을 열고 나서 발행한다
    result = collect_result(
        state=core.READY,
        files=files,
        total_bytes=3,
        bundle_bytes=len(payload),
        skipped_count=0,
        bundle_sha256=digest,
        bundle_path=bundle_dir(cfg, job_id) / "bundle.tar",
    )
    store.publish_bundle(
        job_id, result, now=ready_at, expires_at=core.expires_at(ready_at, ttl_hours)
    )
    return digest


def bundle_state(store: Store, job_id: int) -> str:
    row = store.get_bundle(job_id)
    assert row is not None, job_id
    return row["state"]


# ══ 만료 sweep (§8) ══════════════════════════════════════════════════════════


def test_the_expiry_sweep_deletes_at_the_expiry_moment_and_marks_expired(env):
    core = artifacts_core()
    store, cfg = env
    jid = finished(store, finished_at=NOW - 2 * HOUR)
    payload = b"expired bytes"
    publish(store, cfg, jid, payload=payload, ready_at=NOW - 25 * HOUR)
    assert store.bundle_storage_totals() == (len(payload), 0)
    jan, rec = make_janitor(store, cfg)
    jan.sweep_once(NOW)
    assert not (bundle_dir(cfg, jid) / "bundle.tar").exists()
    assert not bundle_dir(cfg, jid).exists()
    assert bundle_state(store, jid) == core.EXPIRED
    assert store.get_bundle(jid)["purged_at"] == NOW
    assert store.bundle_storage_totals() == (0, 0)
    assert rec.errors == []
    assert store.get_job(jid) is not None  # 잡 기록은 남는다


def test_the_expiry_boundary_is_inclusive_and_a_young_bundle_is_untouched(env):
    core = artifacts_core()
    store, cfg = env
    edge = finished(store, finished_at=NOW - 2 * HOUR)
    publish(store, cfg, edge, ready_at=NOW - TTL_HOURS * HOUR)  # 만료 = NOW
    young = finished(store, finished_at=NOW - 2 * HOUR)
    publish(store, cfg, young, ready_at=NOW - 23 * HOUR)
    jan, rec = make_janitor(store, cfg)
    jan.sweep_once(NOW)
    assert bundle_state(store, edge) == core.EXPIRED
    assert bundle_state(store, young) == core.READY
    assert (bundle_dir(cfg, young) / "bundle.tar").exists()
    assert rec.errors == []


def test_a_bundle_with_no_expiry_is_never_swept(env):
    """만료 없는 행은 절대 대상이 아니다 — 실패·버림은 만료 시각이 없다(§18)."""
    core = artifacts_core()
    store, cfg = env
    jid = finished(store, finished_at=NOW - 10 * DAY)
    store.start_collect(jid, policy(), NOW - 10 * DAY)
    store.set_bundle_failed(jid, core.DROPPED, "over_bytes", {"limit": 1, "seen": 2}, NOW - 9 * DAY)
    assert store.get_bundle(jid)["expires_at"] is None
    jan, rec = make_janitor(store, cfg)
    jan.sweep_once(NOW)
    jan.sweep_once(NOW + 400 * DAY)
    assert bundle_state(store, jid) == core.DROPPED
    assert rec.errors == []


def test_an_already_purged_bundle_is_not_swept_again(env):
    core = artifacts_core()
    store, cfg = env
    jid = finished(store, finished_at=NOW - 2 * HOUR)
    publish(store, cfg, jid, ready_at=NOW - 25 * HOUR)
    store.ack_bundle(jid, store.get_bundle(jid)["bundle_sha256"], owner=True, now=NOW - 20 * HOUR)
    store.mark_bundles_purged([jid], core.PURGED, NOW - 20 * HOUR)
    shutil.rmtree(bundle_dir(cfg, jid))
    jan, rec = make_janitor(store, cfg)
    jan.sweep_once(NOW)
    assert bundle_state(store, jid) == core.PURGED  # 만료가 확인을 덮어쓰지 않는다
    assert store.get_bundle(jid)["purged_at"] == NOW - 20 * HOUR
    assert rec.errors == []


def test_the_bundle_sweep_uses_the_existing_retention_sweep_interval(env):
    """주기는 기존 `retention_sweep_interval_seconds` 를 쓴다 — 새 주기를 만들지 않는다(§8)."""
    store, cfg = env
    cfg.server.retention_sweep_interval_seconds = 3600
    jan, _rec = make_janitor(store, cfg)
    assert jan.interval == 3600


# ══ M3 와의 경계 (§8) — 이 파일이 존재하는 이유 ══════════════════════════════


def test_retention_days_success_zero_does_not_destroy_a_bundle_before_its_ttl(tmp_path):
    """**결정 40 위반을 막는 시험이다.** 보존 0일이어도 24시간 약속은 지켜진다."""
    core = artifacts_core()
    cfg = make_config(tmp_path, retention_days_success=0, retention_days_failure=0)
    for key, value in ARTIFACT_CONFIG.items():
        setattr(cfg.server, key, value)
    store = Store(cfg.data_dir / "rcm.sqlite3")
    try:
        jid = finished(store, finished_at=NOW - timedelta(minutes=1))
        job_dir, ws = touch_dirs(cfg, jid)
        payload = b"golden pngs that must survive"
        publish(store, cfg, jid, payload=payload, ready_at=NOW - timedelta(minutes=1))
        jan, rec = make_janitor(store, cfg)
        # 보존 0일 → M3 는 지금 로그·워크스페이스를 지운다. 번들은 건드리지 않는다.
        assert jan.sweep_once(NOW) == 1
        assert not job_dir.exists() and not ws.exists()
        assert store.get_job(jid).artifacts_purged_at == NOW
        assert (bundle_dir(cfg, jid) / "bundle.tar").read_bytes() == payload
        assert bundle_state(store, jid) == core.READY
        assert store.bundle_storage_totals() == (len(payload), 0)
        assert rec.errors == []
        # 몇 시간이 지나도 그대로다 — 낸 세션이 아직 안 가져갔을 수 있다
        jan.sweep_once(NOW + 23 * HOUR)
        assert (bundle_dir(cfg, jid) / "bundle.tar").exists()
        assert bundle_state(store, jid) == core.READY
        # 24시간이 차야 사라진다
        jan.sweep_once(NOW + TTL_HOURS * HOUR)
        assert bundle_state(store, jid) == core.EXPIRED
        assert not (bundle_dir(cfg, jid) / "bundle.tar").exists()
        assert store.bundle_storage_totals() == (0, 0)
    finally:
        store.close()


def test_m3_cleanup_never_removes_the_artifacts_directory_of_a_live_bundle(env):
    """M3 청소는 번들이 **이미 `purged`·`expired` 일 때만** 그 디렉터리를 지운다(§8).
    잡이 끝난 지 3일이라 로그·워크스페이스는 가지만 번들은 자기 시계로만 사라진다."""
    core = artifacts_core()
    store, cfg = env
    jid = finished(store, finished_at=NOW - 3 * DAY)
    job_dir, ws = touch_dirs(cfg, jid)
    payload = b"still within its ttl"
    publish(store, cfg, jid, payload=payload, ready_at=NOW - 2 * HOUR)
    jan, rec = make_janitor(store, cfg)
    assert jan.sweep_once(NOW) == 1  # 로그·워크스페이스만 간다
    assert not job_dir.exists() and not ws.exists()
    assert (bundle_dir(cfg, jid) / "bundle.tar").read_bytes() == payload
    assert bundle_state(store, jid) == core.READY
    assert store.bundle_storage_totals() == (len(payload), 0)
    assert rec.errors == []
    # TTL 이 차면 번들 sweep 이 디렉터리째 가져간다
    jan.sweep_once(NOW + TTL_HOURS * HOUR)
    assert bundle_state(store, jid) == core.EXPIRED
    assert not bundle_dir(cfg, jid).exists()


def test_metadata_deletion_waits_until_the_bundle_is_really_gone(env):
    """`delete_old_jobs` 는 지금 `jobs.artifacts_purged_at` 만 본다(`store.py:475`) — 삭제에
    실패한 번들이 소유 기록과 회계를 잃으면 안 된다(§8)."""
    core = artifacts_core()
    store, cfg = env
    cfg.server.metadata_retention_days = 1
    jid = finished(store, finished_at=NOW - 10 * DAY)
    # 잡은 열흘 전에 끝났지만 번들은 닷새 전에 발행됐다 — 첫 sweep 때 TTL 안이라 살아 있다.
    # (열흘 전에 발행하면 그 sweep 이 정당하게 만료시켜 버려서 이 시험의 전제가 무너진다.)
    publish(store, cfg, jid, ready_at=NOW - 5 * DAY)
    jan, rec = make_janitor(store, cfg)
    jan.sweep_once(NOW - 5 * DAY)  # 번들은 아직 파일이 있다
    assert store.get_job(jid) is not None
    assert store.get_bundle(jid) is not None
    store.mark_bundles_purged([jid], core.EXPIRED, NOW - 5 * DAY)
    shutil.rmtree(bundle_dir(cfg, jid), ignore_errors=True)
    jan.sweep_once(NOW)
    assert store.get_job(jid) is None
    assert store.get_bundle(jid) is None
    assert rec.errors == []


def test_existing_log_and_workspace_retention_is_unchanged(env):
    """M3 회귀 — 번들이 없는 잡은 예전 그대로 지워지고 표시된다."""
    store, cfg = env
    ok = finished(store, finished_at=NOW - 1.5 * DAY)
    bad = finished(store, state=FAILED, finished_at=NOW - 1.5 * DAY)
    ok_dirs = touch_dirs(cfg, ok)
    bad_dirs = touch_dirs(cfg, bad)
    jan, rec = make_janitor(store, cfg)
    assert jan.sweep_once(NOW) == 1
    assert not ok_dirs[0].exists() and not ok_dirs[1].exists()
    assert bad_dirs[0].exists() and bad_dirs[1].exists()
    assert store.get_job(ok).artifacts_purged_at == NOW
    assert store.get_job(bad).artifacts_purged_at is None
    assert jan.purged_total == 1 and jan.last_sweep_at == NOW
    assert rec.errors == []


def test_an_active_job_with_a_bundle_is_never_touched(env):
    """활성 잡은 어떤 시각이 찍혀 있어도 절대 M3 대상이 아니다 — 번들이 있어도 마찬가지다."""
    store, cfg = env
    job = new_job(store, created=NOW - 10 * DAY)
    assert store.claim(1, NOW - 10 * DAY).id == job.id
    job_dir, ws = touch_dirs(cfg, job.id)
    publish(store, cfg, job.id, ready_at=NOW - 2 * HOUR)
    jan, rec = make_janitor(store, cfg)
    assert jan.sweep_once(NOW) == 0
    assert job_dir.exists() and ws.exists()
    assert (bundle_dir(cfg, job.id) / "bundle.tar").exists()
    assert rec.errors == []


# ══ 삭제 실패 (§7) ═══════════════════════════════════════════════════════════


def test_a_failed_unlink_leaves_the_row_unpurged_keeps_the_bytes_and_retries(env, monkeypatch):
    """물리적으로 지우기 전에 지웠다고 말하지 않는다. 예약 바이트를 계속 잡고 다음 sweep 에
    다시 시도한다(§7)."""
    core = artifacts_core()
    store, cfg = env
    victim = finished(store, finished_at=NOW - 2 * HOUR)
    other = finished(store, finished_at=NOW - 2 * HOUR)
    payload = b"cannot be removed"
    publish(store, cfg, victim, payload=payload, ready_at=NOW - 25 * HOUR)
    publish(store, cfg, other, payload=b"goes away", ready_at=NOW - 25 * HOUR)
    broken = {"on": True}
    real_rmtree = shutil.rmtree
    real_unlink = pathlib.Path.unlink
    target = str(bundle_dir(cfg, victim))

    def blocked(path: Any) -> bool:
        text = str(path)
        return broken["on"] and (text == target or text.startswith(target + os.sep))

    def flaky_rmtree(path, *args, **kwargs):
        if blocked(path):
            raise PermissionError(errno.EACCES, "Permission denied", str(path))
        return real_rmtree(path, *args, **kwargs)

    def flaky_unlink(self, *args, **kwargs):
        if blocked(self):
            raise PermissionError(errno.EACCES, "Permission denied", str(self))
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(shutil, "rmtree", flaky_rmtree)
    monkeypatch.setattr(pathlib.Path, "unlink", flaky_unlink)
    jan, rec = make_janitor(store, cfg)
    jan.sweep_once(NOW)
    assert bundle_state(store, victim) == core.READY  # purged 로 표시하지 않는다
    assert store.get_bundle(victim)["purged_at"] is None
    assert (bundle_dir(cfg, victim) / "bundle.tar").exists()
    assert bundle_state(store, other) == core.EXPIRED  # 다른 것은 정상 진행
    stored, reserved = store.bundle_storage_totals()
    assert stored + reserved == len(payload)  # 회계에 남아 있다
    assert rec.errors, "삭제 실패는 표면화한다"
    assert str(cfg.data_dir) not in " ".join(rec.errors)  # 경로는 로그에도 안 넣는다
    assert jan.dead is None
    broken["on"] = False
    jan.sweep_once(NOW + timedelta(seconds=1))
    assert bundle_state(store, victim) == core.EXPIRED
    assert not (bundle_dir(cfg, victim) / "bundle.tar").exists()
    assert store.bundle_storage_totals() == (0, 0)


def test_a_missing_bundle_file_still_closes_the_row_without_error(env):
    """서버 사고로 파일이 먼저 없어졌어도 sweep 은 조용히 행을 닫는다."""
    core = artifacts_core()
    store, cfg = env
    jid = finished(store, finished_at=NOW - 2 * HOUR)
    publish(store, cfg, jid, ready_at=NOW - 25 * HOUR)
    shutil.rmtree(bundle_dir(cfg, jid))
    jan, rec = make_janitor(store, cfg)
    jan.sweep_once(NOW)
    assert bundle_state(store, jid) == core.EXPIRED
    assert store.bundle_storage_totals() == (0, 0)
    assert rec.errors == []


# ══ 만료를 가로지르는 전송 (§6) ══════════════════════════════════════════════


def _recv_headers(sock: socket.socket) -> tuple[bytes, bytes]:
    """헤더 블록이 다 올 때까지 읽는다. (헤더, 이미 읽어 버린 본문 앞부분)."""
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(65536)
        assert chunk, "응답 헤더가 오지 않았다"
        buf += chunk
    head, _, rest = buf.partition(b"\r\n\r\n")
    return head, rest


def _content_length(head: bytes) -> int:
    for line in head.split(b"\r\n"):
        name, _, value = line.partition(b":")
        if name.strip().lower() == b"content-length":
            return int(value.strip())
    raise AssertionError(f"Content-Length 가 없다: {head!r}")


def test_a_download_in_flight_across_the_expiry_finishes_and_the_bytes_stay_accounted(tmp_path):
    """만료가 지나면 새 다운로드는 410 이지만 **이미 열린 전송은 끝나게** 둔다. unlink 로는
    공간이 돌아오지 않으므로 마지막 독자가 닫을 때까지 바이트를 회계에 남긴다(§6).

    기다림 대신 **같은 연결의 다음 응답**을 배리어로 쓴다 — 그 응답이 오면 앞 핸들러는 끝났다.
    """
    from test_server_m5e import ARTIFACT_CONFIG as SERVER_ARTIFACT_CONFIG
    from test_server_m5e import artifact_presets, ready_job
    from test_worker_api import WorkerServer

    core = artifacts_core()
    s = WorkerServer(tmp_path, **SERVER_ARTIFACT_CONFIG)
    held: socket.socket | None = None
    try:
        s.cfg.presets = artifact_presets()
        payload = b"g" * (4 * 1024 * 1024)  # 소켓 버퍼보다 크다 — 안 읽으면 서버가 멈춰 선다
        jid, _sha = ready_job(s, payload=payload)
        jan = Janitor(s.store, s.cfg, now_fn=lambda: s.clock.now)
        held = socket.create_connection(("127.0.0.1", s.port), timeout=15)
        held.sendall(
            f"GET /jobs/{jid}/artifacts/archive HTTP/1.1\r\nHost: x\r\n"
            f"Authorization: Bearer {s.tokens['alice']}\r\n\r\n".encode()
        )
        head, body = _recv_headers(held)
        assert head.split(b"\r\n", 1)[0].split(b" ")[1] == b"200", head[:80]
        length = _content_length(head)
        assert length == len(payload)
        # 전송 도중에 만료가 지난다
        expiry = s.clock.now + TTL_HOURS * HOUR
        jan.sweep_once(expiry)
        assert s.store.get_bundle(jid)["state"] == core.EXPIRED
        stored, reserved = s.store.bundle_storage_totals()
        assert stored + reserved == len(payload), "마지막 독자가 닫기 전에는 회계에 남는다"
        # 새 다운로드는 410 이다
        s.clock.advance(TTL_HOURS * 3600)
        assert s.req("GET", f"/jobs/{jid}/artifacts/archive", token="alice", raw=True)[0] == 410
        # 이미 열린 전송은 끝까지 온다
        while len(body) < length:
            chunk = held.recv(1 << 20)
            assert chunk, f"전송이 {len(body)}/{length} 에서 끊겼다"
            body += chunk
        assert body == payload
        # 배리어: 같은 연결의 다음 응답이 오면 앞 핸들러는 이미 정리를 마쳤다
        held.sendall(b"GET /api/health HTTP/1.1\r\nHost: x\r\n\r\n")
        head2, _ = _recv_headers(held)
        assert head2.split(b"\r\n", 1)[0].split(b" ")[1] == b"200", head2[:80]
        assert s.store.bundle_storage_totals() == (0, 0)
    finally:
        if held is not None:
            held.close()
        s.close()


def test_a_finished_job_keeps_its_summary_when_the_bundle_expires(env):
    """산출물 사정은 `artifacts.reason_code` 로만 말한다 — 잡의 요약은 건드리지 않는다(§3)."""
    store, cfg = env
    jid = finished(store, state=SUCCEEDED, finished_at=NOW - 2 * HOUR)
    before = store.get_job(jid).summary
    publish(store, cfg, jid, ready_at=NOW - 25 * HOUR)
    jan, _rec = make_janitor(store, cfg)
    jan.sweep_once(NOW)
    after = store.get_job(jid)
    assert after.state == SUCCEEDED and after.summary == before
