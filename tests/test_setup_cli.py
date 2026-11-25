"""`rcm setup` 을 다른 프로세스로 끝까지 돌린다 — `python -c "from remote_ci_monitor.setup import
main"`(명령줄 연결은 2라운드, docs/m5t-workplan.md §5 PR4).

운영 보호: 임시 HOME · XDG 넷과 시험 라벨을 쓰고, PATH 에는 부른 인자를 적고 끝나는 가짜
`launchctl` · `systemctl` 만 둔다. 진짜 서비스 관리자는 부르지 않는다.
"""

from __future__ import annotations

import os
import plistlib
import socket
import subprocess
import sys
from pathlib import Path

LABEL = "test.rcm.setup-cli"
RUN = "import sys; from remote_ci_monitor.setup import main; sys.exit(main(sys.argv[1:]))"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _env(tmp_path: Path) -> tuple[dict[str, str], Path]:
    stubs, log = tmp_path / "stubs", tmp_path / "manager-calls.log"
    stubs.mkdir()
    for name in ("launchctl", "systemctl"):
        stub = stubs / name
        stub.write_text(f'#!/bin/sh\necho "{name} $@" >> {log}\nexit 0\n', encoding="utf-8")
        stub.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith("RCM_")}
    home = tmp_path / "home"
    home.mkdir()
    env.update(
        HOME=str(home),
        XDG_CONFIG_HOME=str(tmp_path / "config"),
        XDG_DATA_HOME=str(tmp_path / "data"),
        XDG_STATE_HOME=str(tmp_path / "state"),
        XDG_CACHE_HOME=str(tmp_path / "cache"),
        PATH=f"{stubs}:/usr/bin:/bin:/usr/sbin:/sbin",
        RCM_TOKEN="tok-" + "K4v" * 6,
    )
    return env, log


def _setup(tmp_path: Path, env: dict[str, str], *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", RUN, *argv],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_tr12_tr34_tr36_a_whole_setup_writes_files_and_only_asks_the_manager(tmp_path):
    env, log = _env(tmp_path)
    port = _free_port()
    data = tmp_path / "srv-data"
    done = _setup(
        tmp_path,
        env,
        "--role",
        "coordinator-worker",
        "--bind",
        "127.0.0.1",
        "--port",
        str(port),
        "--data-dir",
        str(data),
        "--label",
        LABEL,
        "--no-advertise",
        "--yes",
    )
    assert done.returncode == 0, done.stderr
    config = tmp_path / "config" / "rcm" / "server.toml"
    assert config.is_file()
    calls = log.read_text().splitlines()
    # TR36 · TR12: 서비스 관리자에게는 읽기 전용 조회만 했다 — 등록도 시작도 없다
    if sys.platform == "darwin":
        assert calls == [f"launchctl print gui/{os.getuid()}/{LABEL}"]
        staged = config.parent / "service" / f"{LABEL}.plist"
        argv0 = plistlib.loads(staged.read_bytes())["ProgramArguments"][0]
        assert "is already loaded" in done.stderr
    else:
        assert {c.split()[2] for c in calls} <= {"is-active", "is-enabled"}
        staged = config.parent / "service" / f"{LABEL}.service"
        line = next(x for x in staged.read_text().splitlines() if x.startswith("ExecStart="))
        argv0 = line.split("=", 1)[1].split()[0]
    # TR34: 서비스 파일은 setup 을 돌린 빌드(이 venv 의 rcm)를 가리킨다
    assert Path(argv0) == Path(sys.executable).parent / "rcm"
    assert "found on PATH" not in done.stderr
    assert not (tmp_path / "home" / "Library" / "LaunchAgents").exists()
    assert not data.exists()  # data_dir 은 서버가 처음 뜰 때 만든다
    assert env["RCM_TOKEN"] not in done.stdout + done.stderr
    assert str(config) in done.stdout


def test_tr28_a_usage_error_from_the_command_line_is_exit_two(tmp_path):
    env, log = _env(tmp_path)
    done = _setup(tmp_path, env, "--role", "coordinator", "--yes")
    assert done.returncode == 2
    assert "--bind" in done.stderr
    assert not log.exists()
    assert not (tmp_path / "config" / "rcm").exists()
