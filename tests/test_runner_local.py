"""로컬 워커 회귀 잠금(M5b-3 §1) — `Worker` 가 `runner.run_job` 위에 올라가도 오늘과 같아야 한다.

`tests/test_worker.py` 의 도우미(`enqueue` · `run_one` · `sh`)로 실제 `Store` 를 거쳐
claim → execute → finish 를 돈다. 잠근 것: summary · failed_step · DB 마커 · phase 전이
(claim 의 materializing → executing → 종료 뒤 None) · `last_output_at` 갱신 · 워크스페이스
(성공 삭제 · 실패 보존) · 취소 `cancelled by <name>` · 타임아웃 `format_limit` · 자재화 실패 ·
`Worker.shutdown()` → lost `server stopped while running` · 그리고 **`Worker.execute` 가
`RunSpec` 을 잡·프리셋·설정에서 만들어 `run_job` 을 부른다**(마지막 것만 러너가 있어야 초록).

명세는 docs/m5b3-workplan.md §1 「로컬 워커」. 벽시계 sleep 은 스크립트 안의 `sleep 1.1` 하나뿐이고
나머지 기다림은 전부 마감 있는 폴링이다.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from remote_ci_monitor import worker as worker_module
from remote_ci_monitor.config import ServerConfig, parse_preset
from remote_ci_monitor.core.model import (
    CANCELLED,
    FAILED,
    LOST,
    PHASE_EXECUTING,
    RUNNING,
    SUCCEEDED,
    TIMED_OUT,
)
from remote_ci_monitor.store import Store
from remote_ci_monitor.worker import Worker, format_limit
from test_worker import ALICE, enqueue, run_one, sh

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
    sh("lastout", "echo a; sleep 1.1; echo b"),
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


def make_config(tmp_path: Path, **server: Any) -> ServerConfig:
    cfg = ServerConfig()
    cfg.server.data_dir = str(tmp_path / "data")
    cfg.server.grace_seconds = 1
    for k, v in server.items():
        setattr(cfg.server, k, v)
    cfg.presets = tuple(parse_preset(p) for p in PRESETS)
    return cfg


@pytest.fixture
def env(tmp_path):
    cfg = make_config(tmp_path)
    store = Store(cfg.data_dir / "rcm.sqlite3")
    yield store, cfg
    store.close()


def wait_for_executing(store: Store, jid: int, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        j = store.get_job(jid)
        if j.state == RUNNING and j.phase == PHASE_EXECUTING:
            return
        time.sleep(0.05)
    raise AssertionError(f"job #{jid} never reached executing")


def log_of(cfg: ServerConfig, jid: int) -> Path:
    return cfg.data_dir / "jobs" / str(jid) / "log.txt"


def workspace_of(cfg: ServerConfig, jid: int) -> Path:
    return cfg.data_dir / "workspaces" / str(jid)


# ── 성공 · 실패 · 마커 · 워크스페이스 ───────────────────────────────────────


def test_success_stores_markers_summary_and_removes_the_workspace(env):
    """test_worker 그대로: succeeded · exit 0 · summary 는 마커 · failed_step None · lane/phase
    None · DB 마커 4개(순서) · 로그에 마커 줄 그대로 · 워크스페이스 삭제 · 전이
    queued→running→succeeded."""
    store, cfg = env
    jid = enqueue(store, cfg, "ok")
    run_one(store, cfg, jid)
    j = store.get_job(jid)
    assert j.state == SUCCEEDED and j.exit_code == 0 and j.summary == "all green"
    assert j.failed_step is None and j.lane is None and j.phase is None
    assert [(m.kind, m.value) for m in store.markers(jid)] == [
        ("steps", "2"),
        ("step", "a"),
        ("step", "b"),
        ("summary", "all green"),
    ]
    assert all(m.at.tzinfo is not None and m.at >= j.started_at for m in store.markers(jid))
    log = log_of(cfg, jid).read_text()
    assert log == "::rcm::steps::2\n::rcm::step::a\nhi\n::rcm::step::b\n::rcm::summary::all green\n"
    assert not workspace_of(cfg, jid).exists()
    assert [t.state for t in j.transitions] == ["queued", "running", "succeeded"]


def test_failure_keeps_the_workspace_and_blames_the_last_step(env):
    """failed · exit 3 · summary 는 마커 · `failed_step` 은 마지막 스텝 · 워크스페이스 보존
    (`keep_workspace_on_failure` 기본 true)."""
    store, cfg = env
    jid = enqueue(store, cfg, "bad")
    run_one(store, cfg, jid)
    j = store.get_job(jid)
    assert j.state == FAILED and j.exit_code == 3 and j.summary == "2 failed"
    assert j.failed_step == "test" and j.phase is None
    assert (workspace_of(cfg, jid) / "hello.txt").exists()


def test_failure_without_keep_workspace_removes_it(tmp_path):
    """`keep_workspace_on_failure = false` 면 실패해도 지운다 — 정리는 워커(호출자)의 몫이다."""
    cfg = make_config(tmp_path, keep_workspace_on_failure=False)
    store = Store(cfg.data_dir / "rcm.sqlite3")
    try:
        jid = enqueue(store, cfg, "bad")
        run_one(store, cfg, jid)
        assert store.get_job(jid).state == FAILED
        assert not workspace_of(cfg, jid).exists()
    finally:
        store.close()


# ── phase · last_output_at ────────────────────────────────────────────────────


def test_phase_is_executing_while_running_and_last_output_advances(env):
    """claim 이 `materializing` 을 놓고 워커가 `executing` 으로 올린다(관찰자 `phase`). 1초 뒤의
    출력은 `last_output_at` 을 앞으로 민다(`started_at` + 1초 이상). 끝나면 phase 는 None 이고
    `last_output_at` 은 남는다."""
    store, cfg = env
    jid = enqueue(store, cfg, "lastout")
    seen: dict[str, Any] = {}

    def observe() -> None:
        wait_for_executing(store, jid)
        seen["running"] = store.get_job(jid)

    run_one(store, cfg, jid, before_wait=observe)
    running = seen["running"]
    assert running.state == RUNNING and running.phase == PHASE_EXECUTING and running.lane == 1
    assert running.last_output_at is not None and running.last_output_at >= running.started_at
    j = store.get_job(jid)
    assert j.state == SUCCEEDED and j.phase is None
    assert j.last_output_at is not None
    assert j.last_output_at - j.started_at >= timedelta(seconds=1)
    assert j.last_output_at <= j.finished_at
    assert log_of(cfg, jid).read_text() == "a\nb\n"


# ── 취소 · 타임아웃 · 정지 ────────────────────────────────────────────────────


def test_cancel_goes_through_cancelling_and_ends_cancelled_by_the_requester(env):
    """`should_cancel` ↔ DB `cancelling`: request_cancel 뒤 워커가 TERM → cancelled ·
    summary `cancelled by alice-laptop` · `cancelled_by` · 전이에 cancelling 이 있다 · 15초 안."""
    store, cfg = env
    jid = enqueue(store, cfg, "cancelme")

    def cancel_soon() -> None:
        wait_for_executing(store, jid)
        assert store.request_cancel(jid, "alice-laptop", datetime.now(UTC), 1) == "cancelling"

    t0 = time.monotonic()
    run_one(store, cfg, jid, before_wait=cancel_soon)
    j = store.get_job(jid)
    assert j.state == CANCELLED and j.cancelled_by == "alice-laptop"
    assert j.summary == "cancelled by alice-laptop" and j.failed_step is None
    assert time.monotonic() - t0 < 15
    assert [t.state for t in j.transitions] == ["queued", "running", "cancelling", "cancelled"]
    log = log_of(cfg, jid).read_text()
    assert "start" in log and "never" not in log


def test_timeout_ends_timed_out_with_the_limit_summary(env):
    """`timeout_seconds = 1` → timed_out · summary 는 `format_limit(1)` = `limit 1s` · `sleep 30`
    을 기다리지 않는다 · 로그에 `never` 없음."""
    store, cfg = env
    jid = enqueue(store, cfg, "slow")
    t0 = time.monotonic()
    run_one(store, cfg, jid)
    took = time.monotonic() - t0
    j = store.get_job(jid)
    assert j.state == TIMED_OUT and j.timeout_seconds == 1
    assert j.summary == format_limit(1) == "limit 1s"
    assert j.cancelled_by is None and took < 15
    log = log_of(cfg, jid).read_text()
    assert "start" in log and "never" not in log


def test_shutdown_marks_the_running_job_lost(env):
    """`should_stop` ↔ `Worker.shutdown()`: 도는 잡은 TERM → `lost` · summary
    `server stopped while running`(test_server 의 잠금과 같다) · 워커 스레드는 곧 끝난다."""
    store, cfg = env
    jid = enqueue(store, cfg, "cancelme")
    stop = threading.Event()
    w = Worker(1, store, cfg, stop=stop)
    w.start()
    try:
        wait_for_executing(store, jid)
        t0 = time.monotonic()
        w.shutdown()
        w.join(timeout=10)
        assert not w.is_alive()
        j = store.get_job(jid)
        assert j.state == LOST and j.summary == "server stopped while running"
        assert j.lane is None and j.phase is None
        assert time.monotonic() - t0 < 5
    finally:
        stop.set()
        w.wake.set()
        w.join(timeout=5)


# ── 출력 · env · 시작 실패 · 자재화 실패 ──────────────────────────────────────


def test_partial_last_line_is_logged_with_a_newline_and_no_markers(env):
    store, cfg = env
    jid = enqueue(store, cfg, "nomarker")
    run_one(store, cfg, jid)
    assert store.get_job(jid).state == SUCCEEDED
    assert log_of(cfg, jid).read_text() == "no newline at end\n"
    assert store.markers(jid) == []


def test_env_passes_inputs_and_rcm_vars_and_runs_in_the_workspace(env):
    store, cfg = env
    jid = enqueue(store, cfg, "env", inputs={"scope": "fast"})
    run_one(store, cfg, jid)
    j = store.get_job(jid)
    log = log_of(cfg, jid).read_text()
    assert j.state == SUCCEEDED
    assert f"scope=fast job={jid} preset=env mode=tree ci=1 ws=" in log
    assert "hello\n" in log


def test_missing_binary_fails_with_null_exit_code_and_the_cannot_start_summary(env):
    """`RunnerError`(시작 실패)는 워커가 failed 로 닫는다 — exit_code None · summary 는
    `cannot start '/nonexistent/binary-xyz'` 로 시작 · 워크스페이스는 실패라 남는다."""
    store, cfg = env
    jid = enqueue(store, cfg, "missing-bin")
    run_one(store, cfg, jid)
    j = store.get_job(jid)
    assert j.state == FAILED and j.exit_code is None and j.phase is None
    assert j.summary.startswith("cannot start '/nonexistent/binary-xyz'")
    assert workspace_of(cfg, jid).exists()


def test_missing_snapshot_fails_without_executing(env):
    """`MaterializeError` 는 워커가 failed 로 보고한다 — summary 는 문구 그대로 · exit_code None ·
    `executing` 에 간 적 없다(전이는 queued→running→failed 뿐, 로그에 출력 없음)."""
    store, cfg = env
    jid = enqueue(store, cfg, "ok")
    (cfg.data_dir / "jobs" / str(jid) / "tree.tar.gz").unlink()
    run_one(store, cfg, jid)
    j = store.get_job(jid)
    assert j.state == FAILED and j.exit_code is None
    assert j.summary == "snapshot file is missing"
    assert [t.state for t in j.transitions] == ["queued", "running", "failed"]
    assert not log_of(cfg, jid).exists() or log_of(cfg, jid).read_bytes() == b""
    assert store.markers(jid) == []


# ── Worker.execute 가 RunSpec 을 만들어 run_job 을 부른다 ─────────────────────


def test_worker_builds_the_runspec_from_job_preset_and_config_and_calls_run_job(env, monkeypatch):
    """§1 「로컬 워커」: `Worker.execute` 는 잡·프리셋·설정에서 `RunSpec` 을 만들고 관찰자와 함께
    `run_job` 을 **한 번** 부른다. 필드: job_id · preset_name · argv(프리셋) · env(프리셋) ·
    env_passthrough · timeout_seconds(잡) · inputs(잡) · requester_label · source(잡) ·
    workspace `<data>/workspaces/<id>` · log_path `<data>/jobs/<id>/log.txt` · grace_seconds(설정).
    관찰자는 프로토콜 네 메서드를 갖는다. 결과는 오늘과 같다(succeeded · all green).
    러너 모듈은 여기서만 들여온다 — 나머지 회귀 잠금은 러너가 없어도 돌아 오늘의 워커를 검증한다."""
    from remote_ci_monitor import runner as runner_module

    store, cfg = env
    calls: list[tuple[Any, Any]] = []
    real_run_job = runner_module.run_job

    def spy(spec, observer, **kw):
        calls.append((spec, observer))
        return real_run_job(spec, observer, **kw)

    monkeypatch.setattr(runner_module, "run_job", spy)
    if hasattr(worker_module, "run_job"):  # `from runner import run_job` 스타일이면 그쪽도
        monkeypatch.setattr(worker_module, "run_job", spy)
    jid = enqueue(store, cfg, "env", inputs={"scope": "fast"})
    run_one(store, cfg, jid)
    j = store.get_job(jid)
    assert j.state == SUCCEEDED, j.summary
    assert len(calls) == 1
    spec, observer = calls[0]
    preset = cfg.preset("env")
    assert isinstance(spec, runner_module.RunSpec)
    assert spec.job_id == jid and spec.preset_name == "env"
    assert tuple(spec.argv) == tuple(preset.argv)
    assert spec.env == preset.env == {"CI": "1"}
    assert tuple(spec.env_passthrough) == tuple(preset.env_passthrough)
    assert spec.timeout_seconds == j.timeout_seconds == preset.timeout_seconds
    assert spec.inputs == {"scope": "fast"}
    assert spec.requester_label == ALICE.label == "alice@laptop"
    assert spec.source.mode == "tree" and spec.source.base_sha == "abc"
    assert Path(spec.workspace) == workspace_of(cfg, jid)
    assert Path(spec.log_path) == log_of(cfg, jid)
    assert spec.grace_seconds == cfg.server.grace_seconds == 1
    for name in ("phase", "output", "should_cancel", "should_stop"):
        assert callable(getattr(observer, name, None)), name
    assert observer.should_cancel() is False and observer.should_stop() is False  # 끝난 잡
