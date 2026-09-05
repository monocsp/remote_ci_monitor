"""루프백 e2e — 진짜 `rcm serve` 프로세스 + 진짜 `rcm run` 이 실제 스크립트를 돌리고
종료 코드 4종을 낸다.

PLAN M0 완료 기준: `rcm run gate` 가 실제 스크립트를 돌리고 0/1/2/3 이 맞다 · 서버를 죽였다 살려도
큐가 남고 실행 중이던 잡은 lost 다.
"""

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

RCM = [sys.executable, "-m", "remote_ci_monitor.cli"]

SERVER_TOML = """
[server]
bind = "127.0.0.1"
port = {port}
data_dir = "{data_dir}"
grace_seconds = 1

[[presets]]
name = "gate"
description = "Loopback gate"
argv = ["sh", "scripts/gate.sh"]
timeout_seconds = 60
expected_seconds = 5
duration_key_inputs = ["scope"]
[[presets.inputs]]
name = "scope"
type = "choice"
choices = ["full", "fast", "fail", "slow"]
default = "full"

[[presets]]
name = "quick-timeout"
argv = ["sh", "-c", "echo start; sleep 30"]
timeout_seconds = 1
"""

GATE_SH = """#!/bin/sh
echo "::rcm::steps::2"
echo "::rcm::step::analyze"
echo "scope=$RCM_INPUT_SCOPE job=$RCM_JOB_ID"
case "$RCM_INPUT_SCOPE" in
  fail) echo "::rcm::step::test"; echo "::rcm::summary::2 tests failed"; exit 1;;
  slow) echo "::rcm::step::test"; sleep 30;;
esac
echo "::rcm::step::test"
echo "::rcm::summary::all green"
exit 0
"""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def rcm(*args: str, env: dict, cwd: Path | None = None, timeout: float = 60):
    return subprocess.run(
        [*RCM, *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout
    )


class ServerProc:
    def __init__(self, tmp_path: Path):
        self.port = free_port()
        self.data_dir = tmp_path / "data"
        self.config = tmp_path / "server.toml"
        self.config.write_text(SERVER_TOML.format(port=self.port, data_dir=self.data_dir))
        self.env = {
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
            "PYTHONUNBUFFERED": "1",
        }
        self.proc: subprocess.Popen | None = None
        self.log = tmp_path / "server.log"

    def token(self, name: str, admin: bool = False) -> str:
        args = ["token", "--config", str(self.config), "add", name] + (["--admin"] if admin else [])
        out = rcm(*args, env=self.env)
        assert out.returncode == 0, out.stderr
        return out.stdout.strip()

    def start(self) -> None:
        self.proc = subprocess.Popen(
            [*RCM, "serve", "--config", str(self.config)],
            env=self.env,
            stdout=self.log.open("ab"),
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.5):
                    return
            except OSError:
                time.sleep(0.1)
        raise AssertionError(f"server did not start:\n{self.log.read_text()}")

    def stop(self, sig=signal.SIGTERM) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(sig)
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()

    def client_env(self, token: str) -> dict:
        return {**self.env, "RCM_SERVER": f"http://127.0.0.1:{self.port}", "RCM_TOKEN": token}


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "tree"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "gate.sh").write_text(GATE_SH)
    (root / "README.md").write_text("loopback\n")
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    return root


@pytest.fixture
def server(tmp_path):
    s = ServerProc(tmp_path)
    yield s
    s.stop()


def last_json(out: str) -> dict:
    lines = [ln for ln in out.strip().splitlines() if ln.startswith("{")]
    assert lines, f"no JSON line in stdout: {out!r}"
    return json.loads(lines[-1])


def test_rcm_run_exit_codes_0_1_2_3_and_restart_leaves_lost(server, tree, tmp_path):
    token = server.token("laptop")
    admin = server.token("admin", admin=True)
    server.start()
    env = server.client_env(token)

    check = rcm("check", env=env)
    assert check.returncode == 0, check.stdout + check.stderr
    assert "ok   server" in check.stdout and "ok   token         laptop" in check.stdout

    # 0 — succeeded
    out = rcm("run", "gate", "-f", "scope=full", "--by", "e2e@loopback", env=env, cwd=tree)
    body = last_json(out.stdout)
    assert out.returncode == 0, out.stderr
    assert body["state"] == "succeeded" and body["summary"] == "all green"
    assert body["wait_exit_code"] == 0 and body["requester"]["label"] == "e2e@loopback"
    log = server.data_dir / "jobs" / str(body["id"]) / "log.txt"
    assert f"scope=full job={body['id']}" in log.read_text()  # 실제 스크립트가 돌았다

    # 1 — failed
    out = rcm("run", "gate", "-f", "scope=fail", env=env, cwd=tree)
    body = last_json(out.stdout)
    assert out.returncode == 1
    assert body["state"] == "failed" and body["failed_step"] == "test"
    assert body["summary"] == "2 tests failed" and body["exit_code"] == 1

    # 2 — timed out
    out = rcm("run", "quick-timeout", env=env, cwd=tree)
    assert out.returncode == 2 and last_json(out.stdout)["state"] == "timed_out"

    # 2 — cancelled (submit without waiting, cancel, then wait)
    out = rcm("run", "gate", "-f", "scope=slow", "--no-wait", env=env, cwd=tree)
    assert out.returncode == 0
    jid = last_json(out.stdout)["job_id"]
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        state = json.loads(
            rcm("wait", "--job", str(jid), "--timeout", "0", env=env).stdout.splitlines()[-1]
        )["state"]
        if state == "running":
            break
        time.sleep(0.2)
    cancel = rcm("cancel", str(jid), env=env)
    assert cancel.returncode == 0 and last_json(cancel.stdout)["state"] == "cancelling"
    out = rcm("wait", "--job", str(jid), env=env)
    assert out.returncode == 2 and last_json(out.stdout)["state"] == "cancelled"

    # 3 — --timeout while still running, then lost after a server restart
    out = rcm("run", "gate", "-f", "scope=slow", "--timeout", "3", env=env, cwd=tree)
    body = last_json(out.stdout)
    assert out.returncode == 3 and body["state"] in ("queued", "running")
    jid = body["id"]
    queued = rcm("run", "gate", "-f", "scope=fast", "--no-wait", "--no-join", env=env, cwd=tree)
    queued_id = last_json(queued.stdout)["job_id"]
    server.stop(signal.SIGKILL)  # 죽였다가
    server.start()  # 살린다
    out = rcm("wait", "--job", str(jid), env=env)
    body = last_json(out.stdout)
    assert out.returncode == 3 and body["state"] == "lost"
    assert body["summary"].startswith("server restarted")
    out = rcm("wait", "--job", str(queued_id), env=env)  # 큐에 남아 있던 잡은 이어서 돈다
    assert out.returncode == 0 and last_json(out.stdout)["state"] == "succeeded"

    # unknown preset / bad input never reach the server: 2
    out = rcm("run", "nope", env=env, cwd=tree)
    assert out.returncode == 2 and "unknown preset" in out.stderr
    out = rcm("run", "gate", "-f", "scope=huge", env=env, cwd=tree)
    assert out.returncode == 2 and "is not one of" in out.stderr

    # admin pause/resume via CLI; non-admin is refused
    assert rcm("pause", env=env).returncode == 2
    assert rcm("pause", env=server.client_env(admin)).returncode == 0
    assert rcm("resume", env=server.client_env(admin)).returncode == 0
    # token list never shows secrets
    listed = rcm("token", "--config", str(server.config), "list", env=server.env)
    assert "laptop" in listed.stdout and token not in listed.stdout


def test_rcm_run_without_server_is_3(tree):
    env = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        "RCM_SERVER": "http://127.0.0.1:1",
        "RCM_TOKEN": "x",
    }
    out = rcm("run", "gate", env=env, cwd=tree, timeout=60)
    assert out.returncode == 2 and "cannot read presets" in out.stderr  # 제출 전 실패는 2
    # 기다리는 중 서버가 없으면 3 — 60초 유예라 단위 테스트(test_client)로 덮는다
