"""워커 — sh 프리셋으로 성공·실패·타임아웃·취소·마커·env·tar 탈출 거부·프리셋 소멸·워커 다운."""

import io
import tarfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from remote_ci_monitor.config import ServerConfig, parse_preset
from remote_ci_monitor.core.model import (
    CANCELLED,
    FAILED,
    QUEUED,
    SUCCEEDED,
    TIMED_OUT,
    Requester,
    Source,
)
from remote_ci_monitor.core.queue import join_key
from remote_ci_monitor.materialize import MaterializeError, extract_tree
from remote_ci_monitor.store import Store
from remote_ci_monitor.worker import Worker, format_limit, tail_lines

ALICE = Requester(name="alice-laptop", label="alice@laptop")


def sh(name: str, script: str, **extra) -> dict:
    return {"name": name, "argv": ["sh", "-c", script], "timeout_seconds": 60, **extra}


PRESETS = [
    sh(
        "ok",
        "echo '::rcm::steps::2'; echo '::rcm::step::a'; echo hi; echo '::rcm::step::b'; "
        "echo '::rcm::summary::all green'; exit 0",
    ),
    sh("bad", "echo '::rcm::step::test'; echo boom; echo '::rcm::summary::2 failed'; exit 3"),
    sh("slow", "echo start; sleep 30; echo never", timeout_seconds=1),
    sh("cancelme", "echo start; sleep 30; echo never"),
    sh("nomarker", "printf 'no newline at end'; exit 0"),
    sh(
        "env",
        "echo scope=$RCM_INPUT_SCOPE job=$RCM_JOB_ID preset=$RCM_PRESET mode=$RCM_SOURCE_MODE "
        "ci=$CI ws=$RCM_WORKSPACE; test -f hello.txt && cat hello.txt",
        env={"CI": "1"},
        inputs=[
            {"name": "scope", "type": "choice", "choices": ["full", "fast"], "default": "full"}
        ],
    ),
    {"name": "missing-bin", "argv": ["/nonexistent/binary-xyz"], "timeout_seconds": 5},
]


def make_config(tmp_path: Path, **server) -> ServerConfig:
    cfg = ServerConfig()
    cfg.server.data_dir = str(tmp_path / "data")
    cfg.server.grace_seconds = 1
    for k, v in server.items():
        setattr(cfg.server, k, v)
    cfg.presets = tuple(parse_preset(p) for p in PRESETS)
    return cfg


def make_tar(path: Path, files: dict[str, bytes]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))


def enqueue(store: Store, cfg: ServerConfig, preset: str, inputs=None, tar_files=None) -> int:
    inputs = inputs or {}
    now = datetime.now(UTC)
    job = store.create_job(
        preset=preset,
        inputs=inputs,
        key=preset,
        concurrency_group=None,
        source=Source(mode="tree", repo="org/app", base_sha="abc", dirty=False, tree_hash="t"),
        requester=ALICE,
        timeout_seconds=cfg.preset(preset).timeout_seconds if cfg.preset(preset) else 5,
        join_key=join_key(preset, inputs, "t"),
        now=now,
        state=QUEUED,
    )
    make_tar(
        cfg.data_dir / "jobs" / str(job.id) / "tree.tar.gz", tar_files or {"hello.txt": b"hello\n"}
    )
    return job.id


def run_one(
    store: Store, cfg: ServerConfig, job_id: int, *, timeout=20.0, before_wait=None
) -> Worker:
    """워커 하나를 띄워 잡 하나가 끝날 때까지 기다린다."""
    stop = threading.Event()
    w = Worker(1, store, cfg, stop=stop)
    w.start()
    deadline = time.monotonic() + timeout
    if before_wait:
        before_wait()
    while time.monotonic() < deadline:
        j = store.get_job(job_id)
        if j.is_terminal:
            break
        time.sleep(0.05)
    stop.set()
    w.wake.set()
    w.join(timeout=5)
    return w


@pytest.fixture
def env(tmp_path):
    cfg = make_config(tmp_path)
    store = Store(cfg.data_dir / "rcm.sqlite3")
    yield store, cfg
    store.close()


def test_success_records_markers_summary_and_deletes_workspace(env):
    store, cfg = env
    jid = enqueue(store, cfg, "ok")
    run_one(store, cfg, jid)
    j = store.get_job(jid)
    assert j.state == SUCCEEDED and j.exit_code == 0 and j.summary == "all green"
    assert j.failed_step is None and j.lane is None and j.phase is None
    kinds = [(m.kind, m.value) for m in store.markers(jid)]
    assert kinds == [("steps", "2"), ("step", "a"), ("step", "b"), ("summary", "all green")]
    log = (cfg.data_dir / "jobs" / str(jid) / "log.txt").read_text()
    assert "::rcm::step::a\nhi\n" in log  # 마커 줄도 로그에 그대로 남는다
    assert not (cfg.data_dir / "workspaces" / str(jid)).exists()
    assert [t.state for t in j.transitions] == ["queued", "running", "succeeded"]


def test_failure_keeps_workspace_and_blames_last_step(env):
    store, cfg = env
    jid = enqueue(store, cfg, "bad")
    run_one(store, cfg, jid)
    j = store.get_job(jid)
    assert j.state == FAILED and j.exit_code == 3 and j.summary == "2 failed"
    assert j.failed_step == "test"
    assert (cfg.data_dir / "workspaces" / str(jid) / "hello.txt").exists()


def test_timeout_kills_process_group_and_marks_timed_out(env):
    store, cfg = env
    jid = enqueue(store, cfg, "slow")
    t0 = time.monotonic()
    run_one(store, cfg, jid)
    took = time.monotonic() - t0
    j = store.get_job(jid)
    assert j.state == TIMED_OUT and j.summary == "limit 1s" and j.timeout_seconds == 1
    assert took < 15  # sleep 30 을 기다리지 않았다
    log = (cfg.data_dir / "jobs" / str(jid) / "log.txt").read_text()
    assert "start" in log and "never" not in log


def test_cancel_goes_through_cancelling_and_ends_cancelled(env):
    store, cfg = env
    jid = enqueue(store, cfg, "cancelme")

    def cancel_soon():
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            j = store.get_job(jid)
            if j.state == "running" and j.phase == "executing":
                break
            time.sleep(0.05)
        assert store.request_cancel(jid, "alice-laptop", datetime.now(UTC), 1) == "cancelling"

    t0 = time.monotonic()
    run_one(store, cfg, jid, before_wait=cancel_soon)
    j = store.get_job(jid)
    assert j.state == CANCELLED and j.cancelled_by == "alice-laptop"
    assert j.summary == "cancelled by alice-laptop"
    assert time.monotonic() - t0 < 15
    assert [t.state for t in j.transitions] == ["queued", "running", "cancelling", "cancelled"]


def test_output_without_trailing_newline_is_logged(env):
    store, cfg = env
    jid = enqueue(store, cfg, "nomarker")
    run_one(store, cfg, jid)
    assert store.get_job(jid).state == SUCCEEDED
    assert (cfg.data_dir / "jobs" / str(jid) / "log.txt").read_text() == "no newline at end\n"
    assert store.markers(jid) == []


def test_env_passes_inputs_and_rcm_vars_and_runs_in_workspace(env):
    store, cfg = env
    jid = enqueue(store, cfg, "env", inputs={"scope": "fast"})
    run_one(store, cfg, jid)
    j = store.get_job(jid)
    log = (cfg.data_dir / "jobs" / str(jid) / "log.txt").read_text()
    assert j.state == SUCCEEDED
    assert f"scope=fast job={jid} preset=env mode=tree ci=1 ws=" in log
    assert "hello\n" in log  # cwd 가 워크스페이스라 hello.txt 가 보인다


def test_missing_binary_fails_with_null_exit_code(env):
    store, cfg = env
    jid = enqueue(store, cfg, "missing-bin")
    run_one(store, cfg, jid)
    j = store.get_job(jid)
    assert j.state == FAILED and j.exit_code is None
    assert j.summary.startswith("cannot start '/nonexistent/binary-xyz'")


def test_preset_removed_from_config_fails_the_job(env):
    store, cfg = env
    jid = enqueue(store, cfg, "ok")
    cfg.presets = tuple(p for p in cfg.presets if p.name != "ok")
    run_one(store, cfg, jid)
    j = store.get_job(jid)
    assert j.state == FAILED and j.exit_code is None
    assert j.summary == "preset 'ok' is no longer configured"


def test_tar_escape_is_rejected_and_job_fails(env, tmp_path):
    store, cfg = env
    jid = enqueue(store, cfg, "ok")
    tar_path = cfg.data_dir / "jobs" / str(jid) / "tree.tar.gz"
    make_tar(tar_path, {"../escape.txt": b"x"})
    run_one(store, cfg, jid)
    j = store.get_job(jid)
    assert j.state == FAILED and j.exit_code is None
    assert j.summary.startswith("snapshot rejected: member escapes the workspace")  # 뒤에 멤버 이름
    assert not (tmp_path / "data" / "escape.txt").exists()
    with pytest.raises(MaterializeError):
        extract_tree(tar_path, tmp_path / "ws2")


def test_absolute_symlink_and_garbage_archive_are_rejected(tmp_path):
    p = tmp_path / "abs.tar.gz"
    with tarfile.open(p, "w:gz") as tf:
        info = tarfile.TarInfo("link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tf.addfile(info)
    with pytest.raises(MaterializeError) as e:
        extract_tree(p, tmp_path / "ws")
    assert "snapshot rejected" in str(e.value) and "/etc" not in str(e.value)
    garbage = tmp_path / "garbage.tar.gz"
    garbage.write_bytes(b"not a tarball")
    with pytest.raises(MaterializeError) as e:
        extract_tree(garbage, tmp_path / "ws3")
    assert str(e.value) == "snapshot rejected: not a valid tar.gz"


def test_worker_goes_down_on_store_error_and_closes_the_job(env, monkeypatch):
    store, cfg = env
    jid = enqueue(store, cfg, "ok")
    stop = threading.Event()
    w = Worker(1, store, cfg, stop=stop)
    real_execute = w.execute

    def boom(job):
        raise RuntimeError("disk exploded at /var/data/x")

    w.execute = boom
    w.start()
    w.join(timeout=10)
    info = w.info()
    assert info.state == "down" and info.error == "RuntimeError: disk exploded at <path>"
    j = store.get_job(jid)
    assert j.state == FAILED and j.summary == "worker error: RuntimeError: disk exploded at <path>"
    assert real_execute is not None


def test_paused_worker_does_not_claim(env):
    store, cfg = env
    jid = enqueue(store, cfg, "ok")
    store.set_paused("admin", datetime.now(UTC))
    stop = threading.Event()
    w = Worker(1, store, cfg, stop=stop)
    w.start()
    time.sleep(1.2)
    assert store.get_job(jid).state == QUEUED and w.info().state == "idle"
    store.clear_paused()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not store.get_job(jid).is_terminal:
        time.sleep(0.05)
    stop.set()
    w.join(timeout=5)
    assert store.get_job(jid).state == SUCCEEDED


def test_tail_lines_and_format_limit(tmp_path):
    p = tmp_path / "log.txt"
    assert tail_lines(p) is None
    p.write_text("\n".join(f"line {i}" for i in range(100)) + "\n")
    assert tail_lines(p, 3) == ["line 97", "line 98", "line 99"]
    assert tail_lines(p, 0) == []
    assert len(tail_lines(p, 1000, max_bytes=100)) < 20
    assert format_limit(1200) == "limit 20m" and format_limit(3600) == "limit 1h"
    assert format_limit(90) == "limit 90s" and format_limit(None) == "limit"
