"""M5t-a PR5 — 요청자에게 알리기(결정 248 e · f): `ran_on` · `rcm wait` 끝줄과 산출물 상태 ·
`[[notify]]` 의 `RCM_MACHINE` · `rcm wait --notify`.

명세는 `docs/m5t-workplan.md` §4.6 과 §7 의 TN1~TN7 · TN9~TN11 이다(TN8 은 실기, PR7).
**구현보다 먼저 썼다** — 구현 전에는 빨갛다. 새 이름(`ran_on` · `LocalMachine`)은 시험 안에서
찾는다. 모듈 import 가 깨져 수집에서 죽으면 「무엇이 틀렸나」가 아니라 「없다」만 말하기 때문이다.

네 가지 방식으로 잰다:

- `ran_on` 계산은 순수 함수와 잡 문서 빌더를 `jobfactory.job` 으로 직접 부른다.
- 서버가 그 값을 싣는지는 진짜 in-process 서버(`test_server.Server`)의 `GET /jobs/<id>` 와
  `/api/status` 로 본다. 원격 잡은 store 의 claim 에 워커 이름을 줘서 만든다.
- 끝줄은 `cli.wait_for_job` 을 대본으로 갈아 끼우고 `rcm wait` 의 명령 함수(`cli.cmd_wait`)를
  부른다. 끝줄은 `rcm wait` 의 것이다 — `rcm run` 의 기다림은 골든(`test_nowait_resilience`)이
  잠근 그대로다.
- `--notify` 는 가짜 HTTP 서버(`fake_rcm_http.FakeRcm`)와 `rcm wait` 전체를 돌린다. PATH 를
  스텁 폴더 하나로 바꿔 진짜 OS 알림을 띄우지 않는다.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from fake_rcm_http import FakeRcm
from jobfactory import CFG, MEDIANS, NOW, PRESETS, default_workers, job
from remote_ci_monitor import __version__, cli
from remote_ci_monitor.core import model as model_mod
from remote_ci_monitor.core import status as status_mod
from remote_ci_monitor.core.model import CANCELLED, RUNNING, SCHEDULED, SUCCEEDED
from remote_ci_monitor.core.notify import NotifyRule, notify_env
from remote_ci_monitor.core.queue import compute_queue
from test_notify import FakeRun
from test_server import Server

LOCAL_KEY = "a1" * 8  # 이 서버 머신의 host_key(sha256 앞 16자 흉내)
OTHER_KEY = "b2" * 8  # 다른 물리 머신
JOB_ID = 381


def ran_on_fn() -> Any:
    fn = getattr(status_mod, "ran_on", None)
    assert callable(fn), "core.status.ran_on(job, local) is missing"
    return fn


def local_machine(name: str | None = "macmini", host_key: str | None = LOCAL_KEY) -> Any:
    cls = getattr(model_mod, "LocalMachine", None)
    assert cls is not None, "core.model.LocalMachine is missing"
    return cls(name=name, host_key=host_key)


def this_machine() -> str | None:
    """서버가 로컬 레인 잡에 적을 이름 — `capabilities.local_display_name` 의 자리(gethostname)."""
    return socket.gethostname().split(".")[0] or None


def finished_remote(**over: Any) -> Any:
    state = over.pop("state", SUCCEEDED)
    kw: dict[str, Any] = {
        "created_min": 9,
        "started_min": 8,
        "finished_min": 2,
        "exit_code": 0,
        "worker_name": "build-02",
        "host_key": OTHER_KEY,
    }
    kw.update(over)
    return job(JOB_ID, state=state, **kw)


# ── TN9 · TN11: `ran_on` 계산 ────────────────────────────────────────────────


def test_tn9_ran_on_is_the_worker_for_a_remote_job():
    ran_on = ran_on_fn()
    done = finished_remote()
    assert ran_on(done, local_machine()) == "build-02"
    # 도는 원격 잡도 같다 — 시작했으면 계산한다
    running = job(7, state=RUNNING, started_min=1, worker_name="build-02", host_key=OTHER_KEY)
    assert ran_on(running, local_machine()) == "build-02"


def test_tn9_ran_on_is_the_local_display_name_for_a_local_lane_job():
    ran_on = ran_on_fn()
    local = job(8, state=SUCCEEDED, started_min=3, finished_min=1, host_key=LOCAL_KEY)
    assert local.worker_name is None
    assert ran_on(local, local_machine()) == "macmini"
    # M5n 이전 행(host_key NULL)은 읽을 때 로컬로 푼다(store 의 관례와 같다)
    legacy = job(9, state=SUCCEEDED, started_min=3, finished_min=1, host_key=None)
    assert ran_on(legacy, local_machine()) == "macmini"


def test_tn9_ran_on_is_null_when_the_name_is_unknown_and_never_comes_from_host_key():
    ran_on = ran_on_fn()
    local = job(8, state=SUCCEEDED, started_min=3, finished_min=1, host_key=LOCAL_KEY)
    assert ran_on(local, None) is None  # 서버가 이 머신을 말하지 않았다
    assert ran_on(local, local_machine(name=None)) is None  # gethostname 이 비었다
    assert ran_on(local, local_machine(name="")) is None
    # 다른 물리 머신에서 돈 로컬 레인 잡(데이터 폴더를 옮겼다) — 그 이름을 모른다
    moved = job(10, state=SUCCEEDED, started_min=3, finished_min=1, host_key=OTHER_KEY)
    got = ran_on(moved, local_machine())
    assert got is None, got


def test_tn11_ran_on_is_null_for_a_job_that_never_started():
    ran_on = ran_on_fn()
    never = job(11, state=CANCELLED, created_min=5, finished_min=4, worker_name=None)
    assert ran_on(never, local_machine()) is None
    # 대상 워커가 정해진 잡이라도 시작 전이면 null 이다
    targeted = job(12, state=CANCELLED, created_min=5, finished_min=4, worker_name="build-02")
    assert ran_on(targeted, local_machine()) is None
    doc = status_mod.recent_json(never, local=local_machine())
    assert doc["ran_on"] is None
    assert notify_env(doc, "done")["RCM_MACHINE"] == ""  # 알림의 null 관례: 빈 값


def test_tn2_tn9_every_job_document_builder_carries_ran_on():
    """잡 문서를 만드는 세 빌더(큐 행 · 최근 행 · 예약 행)와 `/api/status` 가 같은 값을 싣는다."""
    local = local_machine()
    running = job(412, state=RUNNING, created_min=2, started_min=1, host_key=LOCAL_KEY)
    waiting = job(413, created_min=1)
    rows = compute_queue(
        [running, waiting],
        workers=default_workers([412]),
        paused=False,
        medians=MEDIANS,
        presets=PRESETS,
        cfg=CFG,
        now=NOW,
    )
    by_id = {r.job.id: status_mod.queue_row_json(r, local=local) for r in rows}
    assert by_id[412]["ran_on"] == "macmini" and by_id[413]["ran_on"] is None
    remote = finished_remote()
    recent = status_mod.recent_json(remote, local=local)
    assert recent["ran_on"] == "build-02" and recent["worker"] == "build-02"
    sched = status_mod.scheduled_json(job(14, state=SCHEDULED, created_min=1))
    assert sched["ran_on"] is None
    # `/api/status`: StatusModel 이 이 머신을 들고 가면 풀의 행마다 같은 함수로 채운다
    pool = model_mod.Pool(
        name="default",
        lanes=1,
        queue=tuple(rows),
        queue_error=None,
        recent=(remote, job(15, state=SUCCEEDED, started_min=5, finished_min=4)),
        recent_error=None,
        recent_count=8,
        medians=MEDIANS,
        medians_error=None,
        hosts=(),
        hosts_error=None,
    )
    server = model_mod.ServerInfo(
        version="0.1.0",
        uptime_seconds=1,
        lanes=1,
        paused=None,
        last_error=None,
        workers=tuple(default_workers([412])),
    )
    doc = status_mod.status_json(
        model_mod.StatusModel(
            generated_at=NOW,
            display_timezone=None,
            server=server,
            presets=(),
            pools=(pool,),
            local_machine=local,
        )
    )
    (p,) = doc["pools"]
    assert [r["ran_on"] for r in p["queue"]] == ["macmini", None]
    assert [r["ran_on"] for r in p["recent"]] == ["build-02", "macmini"]


# ── TN1 · TN2 · TN9: 서버가 싣는 값 ──────────────────────────────────────────


def test_tn2_a_local_lane_job_names_this_machine_and_keeps_worker_null(tmp_path):
    srv = Server(tmp_path, workers=True)
    try:
        status, body = srv.submit()
        assert status == 201, body
        jid = body["job_id"]
        assert srv.upload(jid)[0] == 200
        srv.wait_terminal(jid)
        status, doc = srv.req("GET", f"/jobs/{jid}")
        assert status == 200, doc
        assert doc["worker"] is None  # null 이 로컬이라는 관례는 그대로
        assert doc.get("ran_on", "<no key>") == this_machine(), doc
        status, top = srv.req("GET", "/api/status")
        rows = [r for p in top["pools"] for r in (p["recent"] or []) if r["id"] == jid]
        assert [r.get("ran_on", "<no key>") for r in rows] == [this_machine()]
    finally:
        srv.close()


def test_tn1_tn9_a_remote_job_document_names_its_worker(tmp_path):
    srv = Server(tmp_path, workers=False)
    try:
        status, body = srv.submit()
        assert status == 201, body
        jid = body["job_id"]
        assert srv.upload(jid)[0] == 200
        now = datetime.now(UTC)
        claimed = srv.store.claim(1, now, worker_name="build-02")
        assert claimed is not None and claimed.id == jid
        status, doc = srv.req("GET", f"/jobs/{jid}")
        assert doc["state"] == "running" and doc.get("ran_on") == "build-02", doc
        srv.store.finish(jid, "failed", now=now + timedelta(seconds=5), exit_code=1)
        status, doc = srv.req("GET", f"/jobs/{jid}")
        assert doc["worker"] == "build-02" and doc.get("ran_on") == "build-02", doc
    finally:
        srv.close()


# ── TN5 · TN11: `[[notify]]` 의 RCM_MACHINE ──────────────────────────────────


def test_tn5_notify_env_carries_rcm_machine_and_keeps_rcm_requester():
    row = {
        "id": 412,
        "state": "failed",
        "requester": {"name": "alice-laptop", "label": "alice@laptop"},
        "ran_on": "build-02",
    }
    env = notify_env(row, "slack")
    assert env.get("RCM_MACHINE") == "build-02"
    assert env["RCM_REQUESTER"] == "alice@laptop"
    # 사용자 문자열과 같이 정화한다 — 제어문자가 훅의 env 로 새지 않는다
    assert notify_env({**row, "ran_on": "bui\x00ld\x1b-02"}, "slack")["RCM_MACHINE"] == "build-02"


def test_tn5_the_server_hook_gets_this_machine_for_a_local_lane_job(tmp_path):
    srv = Server(tmp_path, workers=True)
    try:
        fake = FakeRun()
        srv.app.notifier.run = fake
        srv.cfg.notify = (NotifyRule(name="done", argv=("true",)),)
        srv.app.notifier.start()  # 규칙이 없던 서버는 알림 스레드를 띄우지 않았다
        status, body = srv.submit()
        jid = body["job_id"]
        assert srv.upload(jid)[0] == 200
        srv.wait_terminal(jid)
        deadline = time.monotonic() + 5
        while not fake.calls and time.monotonic() < deadline:
            time.sleep(0.02)
        envs = fake.envs()
        assert len(envs) == 1, envs
        (env,) = envs
        assert env["RCM_JOB_ID"] == str(jid)
        assert env.get("RCM_MACHINE") == (this_machine() or "")
        assert env["RCM_REQUESTER"] == "alice@host"
    finally:
        srv.close()


# ── TN1 · TN2 · TN3 · TN4 · TN10 · TN11: `rcm wait` 끝줄 ─────────────────────


def wait_doc(**over: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "id": JOB_ID,
        "state": "succeeded",
        "exit_code": 0,
        "key": "gate",
        "summary": "green",
        "url": f"http://build.example:8787/#/jobs/{JOB_ID}",
        "started_at": "2026-11-26T01:00:00Z",
        "job_seconds": 391,
        "worker": "build-02",
        "ran_on": "build-02",
        "failed_step": None,
        "keep": False,
        "shelf": None,
    }
    doc.update(over)
    return doc


def artifacts(state: str, **over: Any) -> dict[str, Any]:
    return {
        "state": state,
        "file_count": 3 if state == "ready" else None,
        "total_bytes": 2048 if state == "ready" else None,
        "bundle_bytes": None,
        "skipped_count": None,
        "ready_at": None,
        "expires_at": None,
        "purged_at": None,
        "reason_code": None,
        "reason_args": None,
        **over,
    }


def wait_once(monkeypatch, capsys, *, code: int, doc: dict[str, Any]):
    """`rcm wait --job 381` 의 명령 함수 — 서버 대신 대본 문서 하나를 받는다."""
    monkeypatch.setattr(cli, "_client", lambda *a, **kw: object())
    monkeypatch.setattr(cli, "wait_for_job", lambda client, job_id, **kw: (code, doc, None))
    args = argparse.Namespace(job=JOB_ID, timeout=None, poll=True, return_on_action=False)
    rc = cli.cmd_wait(args)
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


def last_json(out: str) -> dict[str, Any]:
    lines = [ln for ln in out.strip().splitlines() if ln.startswith("{")]
    assert lines, f"no JSON line in stdout: {out!r}"
    return json.loads(lines[-1])


def test_tn1_the_end_line_names_the_machine_and_the_time(monkeypatch, capsys):
    rc, out, err = wait_once(monkeypatch, capsys, code=0, doc=wait_doc())
    assert rc == 0
    assert "#381 succeeded on build-02 · 6m 31s" in err.splitlines(), err


def test_tn1_a_failed_job_names_its_failed_step(monkeypatch, capsys):
    doc = wait_doc(state="failed", exit_code=1, failed_step="test", summary="exit 1")
    rc, _out, err = wait_once(monkeypatch, capsys, code=1, doc=doc)
    assert rc == 1
    assert "#381 failed on build-02 · 6m 31s · step test" in err.splitlines(), err


def test_tn2_a_local_lane_job_names_its_machine_on_the_end_line(monkeypatch, capsys):
    doc = wait_doc(worker=None, ran_on="macmini")
    rc, out, err = wait_once(monkeypatch, capsys, code=0, doc=doc)
    assert "#381 succeeded on macmini · 6m 31s" in err.splitlines(), err
    got = last_json(out)
    assert got["worker"] is None and got["ran_on"] == "macmini"


def test_tn4_the_artifact_state_shows_on_the_end_line_and_the_json_keeps_it(monkeypatch, capsys):
    doc = wait_doc(artifacts=artifacts("ready"))
    rc, out, err = wait_once(monkeypatch, capsys, code=0, doc=doc)
    assert rc == 0
    assert "#381 succeeded on build-02 · 6m 31s · artifacts ready" in err.splitlines(), err
    got = last_json(out)
    assert got["artifacts"] == doc["artifacts"]  # wait JSON 의 산출물 상태는 지금 그대로
    assert set(got) - set(doc) == {"job_id", "wait_exit_code"}  # 새 칸을 만들지 않는다


def test_tn3_failed_artifacts_without_fetch_warn_and_keep_the_exit_code(monkeypatch, capsys):
    doc = wait_doc(artifacts=artifacts("failed", reason_code="upload_failed"))
    rc, out, err = wait_once(monkeypatch, capsys, code=0, doc=doc)
    assert rc == 0 and last_json(out)["wait_exit_code"] == 0
    warned = [ln for ln in err.splitlines() if "warning" in ln and "artifacts" in ln]
    assert warned == ["rcm: warning: #381 artifacts failed (upload_failed)"], err


def test_tn10_four_artifact_states_warn_and_two_say_still_collecting(monkeypatch, capsys):
    for state in ("failed", "dropped", "unavailable", "unknown"):
        for code, job_state in ((0, "succeeded"), (1, "failed")):
            doc = wait_doc(state=job_state, exit_code=code, artifacts=artifacts(state))
            rc, _out, err = wait_once(monkeypatch, capsys, code=code, doc=doc)
            assert rc == code, (state, job_state)
            head = "rcm: warning: #381 artifacts"
            warned = [ln for ln in err.splitlines() if ln.startswith(head)]
            assert warned == [f"rcm: warning: #381 artifacts {state}"], (state, err)
            assert "still collecting" not in err
    for state in ("collecting", "uploading"):
        doc = wait_doc(artifacts=artifacts(state))
        rc, _out, err = wait_once(monkeypatch, capsys, code=0, doc=doc)
        assert rc == 0
        notes = [ln for ln in err.splitlines() if "still collecting" in ln]
        assert notes == [
            f"#381 artifacts still collecting ({state}) — see them later with rcm artifacts 381"
        ], (state, err)
        assert "warning" not in err
    for state in ("ready", "empty", "disabled", "pending"):
        doc = wait_doc(artifacts=artifacts(state))
        _rc, _out, err = wait_once(monkeypatch, capsys, code=0, doc=doc)
        assert "warning" not in err and "still collecting" not in err, (state, err)


def test_tn11_a_job_that_never_started_has_no_machine_on_the_end_line(monkeypatch, capsys):
    doc = wait_doc(
        state="cancelled", exit_code=None, started_at=None, job_seconds=None, worker=None
    )
    doc["ran_on"] = None
    rc, out, err = wait_once(monkeypatch, capsys, code=2, doc=doc)
    assert rc == 2
    assert "#381 cancelled" in err.splitlines(), err
    assert " on " not in next(ln for ln in err.splitlines() if ln.startswith("#381 cancelled"))
    assert last_json(out)["ran_on"] is None


# ── TN6 · TN7: `rcm wait --notify` ───────────────────────────────────────────


STUB = "#!/bin/sh\nprintf '%s|' \"$0\" \"$@\" >> '{marker}'\nprintf '\\n' >> '{marker}'\n"


@pytest.fixture
def fake_server(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("XDG_CONFIG_HOME", "RCM_LABEL", "RCM_CONFIG"):
        monkeypatch.delenv(var, raising=False)
    srv = FakeRcm()
    srv.route("GET", "/api/health", {"ok": True, "version": __version__})
    srv.route("GET", f"/jobs/{JOB_ID}", wait_doc(artifacts=artifacts("ready")))
    monkeypatch.setenv("RCM_SERVER", srv.url)
    monkeypatch.setenv("RCM_TOKEN", "alice-token")
    yield srv
    srv.close()


def stub_path(tmp_path: Path, names: tuple[str, ...]) -> tuple[Path, Path]:
    """알림 도구 스텁만 든 PATH 폴더와 스텁이 부른 기록 파일."""
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir(exist_ok=True)
    marker = tmp_path / "notified.txt"
    for name in names:
        exe = bin_dir / name
        exe.write_text(STUB.format(marker=marker))
        exe.chmod(0o755)
    return bin_dir, marker


def run_wait(capsys, *extra: str) -> tuple[int, str, str]:
    try:
        code = cli.main(["wait", "--job", str(JOB_ID), "--poll", *extra])
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 2
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def test_tn7_wait_without_notify_never_calls_the_os_notifier(
    fake_server, monkeypatch, tmp_path, capsys
):
    bin_dir, marker = stub_path(tmp_path, ("osascript", "notify-send"))
    monkeypatch.setenv("PATH", str(bin_dir))
    for plat in ("darwin", "linux"):
        monkeypatch.setattr(sys, "platform", plat)
        code, out, err = run_wait(capsys)
        assert code == 0, err
        assert not marker.exists(), marker.read_text()
        assert "--notify" not in err
    # 대조군: 플래그를 주면 그 OS 의 도구가 한 번 불리고 끝줄을 싣는다 — 스텁이 살아 있다는 증거
    monkeypatch.setattr(sys, "platform", "darwin")
    code, out, err = run_wait(capsys, "--notify")
    assert code == 0, err
    (call,) = marker.read_text().splitlines()
    assert call.startswith(str(bin_dir / "osascript") + "|"), call
    assert "#381 succeeded on build-02 · 6m 31s · artifacts ready" in call
    marker.unlink()
    monkeypatch.setattr(sys, "platform", "linux")
    code, out, err = run_wait(capsys, "--notify")
    assert code == 0, err
    (call,) = marker.read_text().splitlines()
    assert call.startswith(str(bin_dir / "notify-send") + "|"), call
    assert "#381 succeeded on build-02 · 6m 31s · artifacts ready" in call


def test_tn6_wait_notify_without_the_tool_warns_once_and_exits_normally(
    fake_server, monkeypatch, tmp_path, capsys
):
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    for plat, tool in (("darwin", "osascript"), ("linux", "notify-send")):
        monkeypatch.setattr(sys, "platform", plat)
        code, out, err = run_wait(capsys, "--notify")
        assert code == 0, err
        assert last_json(out)["wait_exit_code"] == 0
        warned = [ln for ln in err.splitlines() if "--notify" in ln]
        assert warned == [f"rcm: warning: --notify: {tool} not found — no notification sent"], err


def test_tn6_tn7_notify_is_a_wait_flag_and_not_a_run_flag(capsys):
    """기본은 꺼져 있고(⛔ Q3) `rcm run` 에는 더하지 않는다(§1)."""
    parser = cli.build_parser()
    ns = parser.parse_args(["wait", "--job", "1"])
    assert getattr(ns, "notify", None) is False, ns
    assert parser.parse_args(["wait", "--job", "1", "--notify"]).notify is True
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "gate", "--notify"])
    capsys.readouterr()


# ── P6-1: 알림의 끝 · 문구 · 실패 갈래(PR #273 검토) ─────────────────────────


def wait_notify_once(monkeypatch, capsys, *, code: int, doc: dict[str, Any], events=None):
    """`rcm wait --job 381 --notify` 의 명령 함수. `events` 를 주면 JSON 줄이 나간 때를 적는다."""
    monkeypatch.setattr(cli, "_client", lambda *a, **kw: object())
    monkeypatch.setattr(cli, "wait_for_job", lambda client, job_id, **kw: (code, doc, None))
    if events is not None:
        real = cli._print_json

        def recorded(obj: Any) -> None:
            events.append("json")
            real(obj)

        monkeypatch.setattr(cli, "_print_json", recorded)
    args = argparse.Namespace(
        job=JOB_ID, timeout=None, poll=True, return_on_action=False, notify=True
    )
    escaped = None
    try:
        rc = cli.cmd_wait(args)
    except KeyboardInterrupt as e:  # 시험 세션을 끊지 않게 잡아서 단언으로 말한다
        escaped, rc = e, None
    cap = capsys.readouterr()
    return rc, cap.out, cap.err, escaped


def test_tn7_ctrl_c_during_the_notification_keeps_the_exit_code_and_the_json(
    monkeypatch, tmp_path, capsys
):
    """알림은 결과가 나간 뒤다. 도구를 기다리다 Ctrl-C 가 와도 잡의 종료 코드와 JSON 은 그대로다."""
    bin_dir, marker = stub_path(tmp_path, ("notify-send",))
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setattr(sys, "platform", "linux")
    events: list[str] = []

    def interrupted(*a: Any, **kw: Any) -> Any:
        events.append("notify")
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.subprocess, "run", interrupted)
    doc = wait_doc(state="failed", exit_code=1, failed_step="test", summary="exit 1")
    rc, out, err, escaped = wait_notify_once(monkeypatch, capsys, code=1, doc=doc, events=events)
    assert escaped is None, f"Ctrl-C during the notification escaped rcm wait: {err!r}"
    assert rc == 1
    assert events == ["json", "notify"], events  # JSON 이 먼저 나가고 알림은 그 뒤다
    assert last_json(out)["wait_exit_code"] == 1
    warned = [ln for ln in err.splitlines() if "--notify" in ln]
    assert warned == ["rcm: warning: --notify: interrupted — no notification sent"], err


def capture_notifications(monkeypatch) -> list[tuple[str, str]]:
    sent: list[tuple[str, str]] = []

    def fake(title: str, message: str) -> None:
        sent.append((title, message))
        return None

    monkeypatch.setattr(cli, "os_notify", fake)
    return sent


def test_tn7_a_delivery_failure_is_what_the_notification_says(monkeypatch, tmp_path, capsys):
    """받기 실패로 종료 코드가 5 가 되면 알림도 그 결과를 말한다. 「succeeded」로 시작하지 않는다.

    지금 CLI 에서는 둘이 함께 오지 않는다(`--fetch-artifacts` 는 `rcm run`, `--notify` 는
    `rcm wait`). 그래서 `_wait` 를 직접 불러 알림 문구가 최종 종료 코드를 보는지 잠근다."""
    sent = capture_notifications(monkeypatch)
    done = wait_doc(artifacts=artifacts("ready"))
    monkeypatch.setattr(cli, "wait_for_job", lambda client, job_id, **kw: (0, done, None))
    monkeypatch.setattr(
        cli, "_fetch_artifacts", lambda *a, **kw: {"ok": False, "reason": "write_failed"}
    )
    spec = cli._FetchSpec(root=tmp_path, baseline={})
    code = cli._wait(
        object(), JOB_ID, timeout=None, joined=False, fetch=spec, end_summary=True, notify=True
    )
    out = capsys.readouterr().out
    assert code == cli.EXIT_DELIVERY
    assert last_json(out)["artifact_fetch"]["ok"] is False
    ((title, message),) = sent
    assert title == "rcm"
    assert message.startswith("#381 artifact delivery failed · exit 5"), message
    assert message.endswith("job succeeded on build-02 · 6m 31s · artifacts ready"), message


def test_tn7_notify_send_reads_a_dash_message_as_text(monkeypatch, tmp_path):
    """`notify-send` 인자 앞에 `--` 를 둔다. `-` 로 시작하는 본문이 옵션으로 읽히지 않는다."""
    monkeypatch.setattr(sys, "platform", "linux")
    assert cli._notifier_argv("rcm", "-u critical") == (
        "notify-send",
        ["--", "rcm", "-u critical"],
    )
    bin_dir, marker = stub_path(tmp_path, ("notify-send",))
    monkeypatch.setenv("PATH", str(bin_dir))
    assert cli.os_notify("rcm", "--help") is None
    (call,) = marker.read_text().splitlines()
    assert call == f"{bin_dir / 'notify-send'}|--|rcm|--help|", call


def test_tn11_a_job_that_never_started_says_nothing_about_artifacts(monkeypatch, capsys):
    """돌지 않은 잡에는 올릴 산출물이 없다. `unknown` 경고도, 끝 줄의 산출물 꼬리도 없다."""
    for state in ("unknown", "failed", "collecting"):
        doc = wait_doc(
            state="cancelled",
            exit_code=None,
            started_at=None,
            job_seconds=None,
            worker=None,
            ran_on=None,
            artifacts=artifacts(state),
        )
        rc, out, err = wait_once(monkeypatch, capsys, code=2, doc=doc)
        assert rc == 2
        assert "#381 cancelled" in err.splitlines(), (state, err)
        assert "artifacts" not in err, (state, err)
        assert last_json(out)["artifacts"] == doc["artifacts"]  # JSON 은 그대로 싣는다
