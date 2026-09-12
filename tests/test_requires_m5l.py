"""프리셋 `requires` 의 리뷰 보완(M5l S1~S4) — PR #109 리뷰
(`docs/reviews/2026-09-13-impl-reviews/pr-109.md`)의 재현 조건을 그대로 잠근다.

- S1 상대 PATH 항목의 cwd fail-open: 프로세스는 `cwd=spec.workspace` 로 뜨므로 `tools`·`.`·빈
  항목은 **워크스페이스** 기준이다. 서버 cwd 에만 있는 도구는 `tool_missing`, 워크스페이스 안의
  도구는 통과.
- S2 basename 이 빈 절대경로(`/dir/`·`/`)는 설정 오류 — 공개 이름은 언제나 비지 않은 basename.
- S3 잡 로그도 basename + 판정만 — 선언한 절대경로는 어디에도 없다(명세 §2 G4).
- S4 preflight 전에 취소·종료 요청이 있었으면 그것이 이긴다 — `cancelled`/`lost` 이지
  `tool_missing` 이 아니다. 로컬·원격·서버 finish 모두.

구현 전이라 빨간 것이 정상이다. 명세 `docs/gate-optimization-workplan.md` §2 G4 · 결정 85 ·
`docs/m5l-review-supplement-workplan.md` S1~S4.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import UTC, datetime

import pytest

from remote_ci_monitor.config import ConfigError, WorkerConfig, parse_preset
from remote_ci_monitor.core.model import CANCELLED, CANCELLING, LOST
from remote_ci_monitor.remote_worker import RemoteWorker
from remote_ci_monitor.runner import RequiredToolMissing, run_job
from remote_ci_monitor.store import Store
from remote_ci_monitor.worker import Worker
from test_requires import MISSING, _Observer, fake_tool, spec_for
from test_server import make_tar, sh
from test_worker import enqueue, make_config, run_one
from test_worker_api import WorkerServer, running_job
from test_worker_m5e import FakeWorkerClient, claim_payload

STARTED_ARGV = ("sh", "-c", "echo STARTED")


def preflight_lines(log: str) -> list[str]:
    return [ln for ln in log.splitlines() if ln.startswith("[rcm] required tool")]


# ── S1 · 상대 PATH 항목은 워크스페이스 기준 ─────────────────────────────────


def test_a_tool_only_in_the_servers_cwd_is_missing_for_the_job(tmp_path, monkeypatch):
    """리뷰 B P1 재현: `PATH="tools:…"`, 서버 cwd 에 `tools/fvm`, 워크스페이스엔 없음 → 프로세스는
    워크스페이스에서 뜨니 `fvm` 은 없다. 검사가 서버 cwd 를 보면 거짓으로 통과한다(fail-open)."""
    server_cwd = tmp_path / "server-cwd"
    fake_tool(server_cwd / "tools", "fvm")
    monkeypatch.chdir(server_cwd)
    spec = spec_for(tmp_path, requires=("fvm",))
    with pytest.raises(RequiredToolMissing) as e:
        run_job(spec, _Observer(), environ={"PATH": f"tools{os.pathsep}/usr/bin:/bin"})
    assert e.value.public_name == "fvm"
    assert "STARTED" not in spec.log_path.read_text()


def test_a_tool_inside_the_workspace_is_found_through_a_relative_path_entry(tmp_path, monkeypatch):
    """반대 배치: 워크스페이스 안에 `tools/fvm`, 서버 cwd 엔 없음 → 통과(거짓 실패 금지)."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    spec = spec_for(tmp_path, requires=("fvm",))
    fake_tool(spec.workspace / "tools", "fvm")
    result = run_job(spec, _Observer(), environ={"PATH": f"tools{os.pathsep}/usr/bin:/bin"})
    assert result.rc == 0
    log = spec.log_path.read_text()
    assert "[rcm] required tools: fvm ok" in log and "STARTED" in log


@pytest.mark.parametrize("path", [":/usr/bin:/bin", "/usr/bin::/bin", "/usr/bin:/bin:"])
def test_an_empty_path_entry_means_the_workspace(tmp_path, monkeypatch, path):
    """빈 구성요소(앞·중간·뒤의 `:`)는 POSIX 의 「현재 디렉터리」 — 잡에게는 워크스페이스다."""
    server_cwd = tmp_path / "server-cwd"
    fake_tool(server_cwd, "fvm")
    monkeypatch.chdir(server_cwd)
    spec = spec_for(tmp_path, requires=("fvm",))
    with pytest.raises(RequiredToolMissing):
        run_job(spec, _Observer(), environ={"PATH": path})
    fake_tool(spec.workspace, "fvm")
    assert run_job(spec, _Observer(), environ={"PATH": path}).rc == 0


def test_a_dot_path_entry_means_the_workspace(tmp_path, monkeypatch):
    server_cwd = tmp_path / "server-cwd"
    fake_tool(server_cwd, "fvm")
    monkeypatch.chdir(server_cwd)
    spec = spec_for(tmp_path, requires=("fvm",))
    with pytest.raises(RequiredToolMissing):
        run_job(spec, _Observer(), environ={"PATH": f".{os.pathsep}/usr/bin:/bin"})
    fake_tool(spec.workspace, "fvm")
    assert run_job(spec, _Observer(), environ={"PATH": f".{os.pathsep}/usr/bin:/bin"}).rc == 0


# ── S2 · basename 없는 절대경로는 설정 오류 ──────────────────────────────────


@pytest.mark.parametrize("value", ["/dir/", "/", "/opt/bin/", "/opt/.", "/opt/.."])
def test_requires_rejects_an_absolute_path_without_a_tool_name(value):
    """리뷰 B P1: basename 이 비면 `public_name` 이 경로 전체로 물러나 `/api/status.recent` 에
    서버 경로가 실렸다. 설정에서 막는다 — 공개 이름은 언제나 비지 않은 basename."""
    with pytest.raises(ConfigError) as e:
        parse_preset({"name": "gate", "argv": ["x"], "requires": [value]})
    msg = str(e.value)
    assert "preset 'gate'" in msg and "requires" in msg, msg
    assert "tool name" in msg, msg


def test_the_public_name_of_an_absolute_declaration_is_its_basename():
    e = RequiredToolMissing("/opt/private/toolchains/fvm")
    assert e.public_name == "fvm"
    assert "/" not in str(e)


# ── S3 · 잡 로그도 basename + 판정만 ─────────────────────────────────────────


def test_the_job_log_names_a_missing_absolute_declaration_by_its_basename(tmp_path):
    """리뷰 B P1: `requires=["/opt/private/toolchains/fvm"]` 인 잡의 로그에 원문 절대경로가 남았다.
    명세 §2 G4 는 로그도 이름과 판정만이라고 한다."""
    spec = spec_for(tmp_path, requires=("/opt/private/toolchains/fvm",))
    with pytest.raises(RequiredToolMissing):
        run_job(spec, _Observer(), environ={"PATH": "/usr/bin:/bin"})
    lines = preflight_lines(spec.log_path.read_text())
    assert lines == ["[rcm] required tool fvm: missing"], lines


def test_the_job_log_names_a_found_absolute_declaration_by_its_basename(tmp_path):
    private = tmp_path / "private-toolchains"
    gitleaks = fake_tool(private, "gitleaks")
    spec = spec_for(tmp_path, requires=("sh", str(gitleaks)))
    obs = _Observer()
    assert run_job(spec, obs, environ={"PATH": "/usr/bin:/bin"}).rc == 0
    lines = preflight_lines(spec.log_path.read_text())
    assert lines == ["[rcm] required tools: sh ok · gitleaks ok"], lines
    assert str(private) not in obs.out.decode()


def test_no_path_component_of_the_jobs_path_reaches_the_log(tmp_path):
    """리뷰 C: `os.environ["PATH"] not in log` 는 전체 문자열만 본다 — 구성요소 하나하나가 없어야
    한다."""
    secret = tmp_path / "secret-bin"
    fake_tool(secret, "present-tool")
    path = f"{secret}{os.pathsep}/usr/bin{os.pathsep}/bin"
    spec = spec_for(tmp_path, requires=("present-tool", MISSING))
    with pytest.raises(RequiredToolMissing):
        run_job(spec, _Observer(), environ={"PATH": path})
    log = spec.log_path.read_text()
    for component in path.split(os.pathsep):
        assert component not in log, (component, log)


# ── S4 · preflight 전의 취소·종료 요청이 이긴다 ──────────────────────────────


class _CancelledObserver(_Observer):
    def __init__(self, *, cancel: bool = False, stop: bool = False) -> None:
        super().__init__()
        self.cancel, self.stop = cancel, stop

    def should_cancel(self) -> bool:
        return self.cancel

    def should_stop(self) -> bool:
        return self.stop


def test_a_cancel_seen_before_the_preflight_returns_cancelled_without_a_verdict(tmp_path):
    """리뷰 B P2: 자재화 중 취소 → 자재화가 끝난 뒤 preflight 가 누락을 찾아 `tool_missing` 으로
    덮었다. preflight 직전에 취소를 보면 프로세스도 판정 줄도 없이 `cancelled` 다."""
    spec = spec_for(tmp_path, requires=(MISSING,))
    obs = _CancelledObserver(cancel=True)
    result = run_job(spec, obs, environ={"PATH": "/usr/bin:/bin"})
    assert result.cancelled and result.rc is None
    assert not result.lost and not result.timed_out
    log = spec.log_path.read_text()
    assert preflight_lines(log) == [] and "STARTED" not in log
    assert obs.phases == []  # `executing` 에 들어가지 않았다


def test_a_stop_seen_before_the_preflight_returns_lost(tmp_path):
    spec = spec_for(tmp_path, requires=(MISSING,))
    result = run_job(spec, _CancelledObserver(stop=True), environ={"PATH": "/usr/bin:/bin"})
    assert result.lost and result.rc is None and not result.cancelled
    assert "STARTED" not in spec.log_path.read_text()


@pytest.fixture
def wenv(tmp_path):
    cfg = make_config(tmp_path)
    cfg.presets = (
        *cfg.presets,
        parse_preset(sh("needs-missing", "echo STARTED", requires=["sh", MISSING])),
    )
    store = Store(cfg.data_dir / "rcm.sqlite3")
    yield store, cfg
    store.close()


def _cancel_when_running(store: Store, jid: int, by: str = "alice-laptop") -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if store.get_job(jid).state == "running":
            break
        time.sleep(0.02)
    assert store.request_cancel(jid, by, datetime.now(UTC), 1) == CANCELLING


def test_a_job_cancelled_while_materialising_ends_cancelled_not_tool_missing(wenv, monkeypatch):
    """로컬 워커: 자재화가 막혀 있는 동안 취소 → 자재화가 풀린 뒤 없는 도구 → `cancelled`(요청자
    이름) 이지 `tool_missing` 이 아니다. 판정 줄도 STARTED 도 없다."""
    store, cfg = wenv
    jid = enqueue(store, cfg, "needs-missing")
    gate = threading.Event()
    monkeypatch.setattr(Worker, "_materialize", lambda self, *a: gate.wait(10))

    def cancel_then_release() -> None:
        _cancel_when_running(store, jid)
        gate.set()

    run_one(store, cfg, jid, before_wait=cancel_then_release)
    j = store.get_job(jid)
    assert j.state == CANCELLED and j.exit_code is None, (j.state, j.summary)
    assert j.summary_code == "cancelled_by" and j.summary_args == {"by": "alice-laptop"}
    assert j.cancelled_by == "alice-laptop"
    assert [t.state for t in j.transitions] == ["queued", "running", "cancelling", "cancelled"]
    log = (cfg.data_dir / "jobs" / str(jid) / "log.txt").read_text()
    assert preflight_lines(log) == [] and "STARTED" not in log


def test_a_cancel_that_lands_between_the_check_and_the_finish_still_wins(wenv, monkeypatch):
    """경쟁의 마지막 틈: 취소 확인 뒤·finish 전에 `cancelling` 이 됐다 — `only_from=("running",)`
    이 `failed` 를 거절하고 잡은 `cancelled` 로 닫힌다."""
    import remote_ci_monitor.runner as runner_mod

    store, cfg = wenv
    jid = enqueue(store, cfg, "needs-missing")
    real = runner_mod.missing_tools

    def cancel_then_check(*args, **kwargs):
        assert store.request_cancel(jid, "alice-laptop", datetime.now(UTC), 1) == CANCELLING
        return real(*args, **kwargs)

    monkeypatch.setattr(runner_mod, "missing_tools", cancel_then_check)
    run_one(store, cfg, jid)
    j = store.get_job(jid)
    assert j.state == CANCELLED and j.exit_code is None, (j.state, j.summary)
    assert j.summary_code == "cancelled_by" and j.summary_args == {"by": "alice-laptop"}
    assert [t.state for t in j.transitions] == ["queued", "running", "cancelling", "cancelled"]


# ── S4 · 원격 워커와 서버 finish ─────────────────────────────────────────────


@pytest.fixture
def remote(tmp_path):
    fake = FakeWorkerClient(make_tar({"hello.txt": b"hello\n"}))
    cfg = WorkerConfig(
        server="http://fake",
        token="t",
        pool="default",
        lanes=1,
        data_dir=str(tmp_path / "wdata"),
        grace_seconds=1,
    )
    worker = RemoteWorker(cfg, client=fake, log=lambda msg: None, environ=dict(os.environ))
    fake.worker = worker
    return worker, fake


def _claim_needing(job_id: int, *tools: str) -> dict:
    payload = claim_payload(job_id, argv=list(STARTED_ARGV), globs=())
    payload["preset"]["requires"] = list(tools)
    return payload


def test_the_remote_worker_reports_cancelled_when_the_cancel_arrived_first(remote):
    """heartbeat 로 취소를 이미 받은 잡: preflight 가 누락을 찾아도 finish 는 `cancelled` 다."""
    worker, fake = remote
    worker.cancel_requested.add(7)
    worker.run_claimed(1, _claim_needing(7, MISSING))
    assert fake.finish_kwargs["outcome"] == CANCELLED, fake.finish_kwargs
    assert "summary_code" not in fake.finish_kwargs, fake.finish_kwargs
    assert worker.running == {}


def test_the_remote_worker_reports_lost_when_stopping_before_the_preflight(remote):
    worker, fake = remote
    worker.stopping.set()
    worker.run_claimed(1, _claim_needing(7, MISSING))
    assert fake.finish_kwargs["outcome"] == LOST, fake.finish_kwargs
    assert "summary_code" not in fake.finish_kwargs, fake.finish_kwargs


@pytest.fixture
def wsrv(tmp_path):
    s = WorkerServer(tmp_path)
    yield s
    s.close()


def test_the_server_closes_a_cancelling_job_as_cancelled_over_a_tool_missing_finish(wsrv):
    """워커가 취소를 아직 못 들은 채 `tool_missing` 을 보고해도 서버는 `cancelling` 을 `failed`
    로 덮지 않는다 — 프로세스는 뜨지 않았고 사용자는 취소했다."""
    jid = running_job(wsrv)
    assert wsrv.cancel(jid)[1] == {"job_id": jid, "state": CANCELLING}
    status, body = wsrv.req(
        "POST",
        f"/worker/jobs/{jid}/finish",
        token="build-02",
        json_body={
            "outcome": "failed",
            "exit_code": None,
            "summary_code": "tool_missing",
            "summary_args": {"tool": "fvm"},
        },
    )
    assert status == 200, body
    v = wsrv.view(jid)
    assert v["state"] == CANCELLED and v["exit_code"] is None, v
    assert v["summary_code"] == "cancelled_by" and v["summary_args"] == {"by": "alice-laptop"}
    assert v["failed_step"] is None and v["last_step"] is None
    assert wsrv.worker_lane("build-02", 1)["state"] == "idle"
