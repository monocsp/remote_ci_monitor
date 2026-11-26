"""`rcm setup` 과 인자 없는 `rcm` 의 첫 실행 질문을 명령줄로 끝까지 돌린다(docs/m5t-workplan.md
§4.1 · §5 PR4).

다른 프로세스로 도는 시험은 `python -c "from remote_ci_monitor.setup import main"` 이나 이 venv 의
`rcm` 실행 파일을 부른다. 첫 실행 질문은 이 프로세스에서 `cli.main([])` 을 TTY 흉내 스트림으로
부르고, 하나는 진짜 의사 터미널(pty)로 부른다.

운영 보호: 임시 HOME · XDG 넷과 시험 라벨을 쓰고, PATH 에는 부른 인자를 적고 끝나는 가짜
`launchctl` · `systemctl` 만 둔다. 진짜 서비스 관리자는 부르지 않는다. 셸의 `RCM_*` 는 지운다.
"""

from __future__ import annotations

import io
import os
import plistlib
import pty
import select
import socket
import sqlite3
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import pytest

from remote_ci_monitor import cli
from remote_ci_monitor.store import DB_VERSION

LABEL = "test.rcm.setup-cli"
RUN = "import sys; from remote_ci_monitor.setup import main; sys.exit(main(sys.argv[1:]))"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


#: 시험의 setup 이 보는 시스템 자리 — 진짜 `/Library/Launch*` · `/etc/systemd` 대신(batch 10 M6)
SYSTEM_PLACE_NAMES = ("LaunchAgents", "LaunchDaemons", "system")


def _site(tmp_path: Path) -> Path:
    """다른 프로세스의 setup 이 진짜 시스템 자리와 passwd 의 홈을 읽지 않게 하는
    `sitecustomize.py` 가 든 폴더 — `PYTHONPATH` 에 둔다(batch 10 M6). setup 에는 환경 변수로 스캔
    자리를 바꾸는 길이 없다(운영 우회가 된다)."""
    site = tmp_path / "site"
    site.mkdir(exist_ok=True)
    places = ", ".join(f"Path({str(tmp_path / 'sys' / n)!r})" for n in SYSTEM_PLACE_NAMES)
    (site / "sitecustomize.py").write_text(
        "from pathlib import Path\n"
        "from remote_ci_monitor import setup as _setup\n"
        f"_setup.SYSTEM_SERVICE_PLACES = ({places},)\n"
        f"_setup.account_home = lambda: Path({str(tmp_path / 'account-home')!r})\n",
        encoding="utf-8",
    )
    return site


@pytest.fixture(autouse=True)
def _no_system_places(tmp_path, monkeypatch):
    """이 프로세스에서 도는 setup(첫 실행 질문)도 같다."""
    from remote_ci_monitor import setup

    places = tuple(tmp_path / "sys" / n for n in SYSTEM_PLACE_NAMES)
    monkeypatch.setattr(setup, "SYSTEM_SERVICE_PLACES", places)
    monkeypatch.setattr(setup, "account_home", lambda: tmp_path / "account-home")


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
        # 덮지 않고 앞에 붙인다 — 셸의 PYTHONPATH 가 이 venv 밖의 패키지를 줄 수 있다(batch 11 L6)
        PYTHONPATH=os.pathsep.join(
            p for p in (str(_site(tmp_path)), os.environ.get("PYTHONPATH", "")) if p
        ),
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


# ── 명령줄의 `rcm setup` (cli.build_parser 에 붙은 하위 명령) ────────────────


RCM = Path(sys.executable).parent / "rcm"


def _rcm(tmp_path: Path, env: dict[str, str], *argv: str) -> subprocess.CompletedProcess:
    """이 venv 의 `rcm` 실행 파일 — 콘솔 스크립트의 진입(따라가기 판단 포함)부터 돈다."""
    return subprocess.run(
        [str(RCM), *argv],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_tr26_tr35_rcm_setup_worker_with_yes_from_the_command_line(tmp_path):
    env, log = _env(tmp_path)
    label = "test.rcm.setup-cli-worker"
    base = ["setup", "--role", "worker", "--yes", "--label", label]
    # TR35: --yes 의 worker 는 --server · --pool · --lanes 가 다 있어야 한다
    for drop in ("--server", "--pool", "--lanes"):
        given = {"--server": "http://10.0.0.5:18790", "--pool": "p1", "--lanes": "2"}
        given.pop(drop)
        flat = [x for kv in given.items() for x in kv]
        done = _rcm(tmp_path, env, *base, *flat)
        assert done.returncode == 2, (drop, done.stderr)
        assert drop in done.stderr, done.stderr
    assert not (tmp_path / "config" / "rcm").exists()
    done = _rcm(
        tmp_path,
        env,
        *base,
        "--server",
        "http://10.0.0.5:18790/",
        "--pool",
        "p1",
        "--lanes",
        "2",
    )
    assert done.returncode == 0, done.stderr
    config = tmp_path / "config" / "rcm" / "worker.toml"
    worker = tomllib.loads(config.read_text(encoding="utf-8"))
    assert worker["server"] == "http://10.0.0.5:18790"  # TR26: auto 가 아니다
    assert (worker["pool"], worker["lanes"]) == ("p1", 2)
    assert oct(config.stat().st_mode & 0o777) == "0o600"
    assert str(config) in done.stdout
    # 역할별 토큰 안내: 코디네이터에서 워커 토큰을 만들어 파일의 token 으로 넣는다
    assert "rcm token add WORKER_NAME --worker" in done.stderr
    assert "token =" in done.stderr and "RCM_WORKER_TOKEN" in done.stderr
    assert env["RCM_TOKEN"] not in done.stdout + done.stderr
    service = config.parent / "service"
    if sys.platform == "darwin":
        staged = service / f"{label}.plist"
        assert plistlib.loads(staged.read_bytes())["Label"] == label
        assert log.read_text().splitlines() == [f"launchctl print gui/{os.getuid()}/{label}"]
    else:
        assert (service / f"{label}.service").is_file()
    assert not (tmp_path / "home" / "Library" / "LaunchAgents").exists()
    assert "rcm doctor --role worker" in done.stderr.strip().splitlines()[-1]


def test_tr35_tr23_the_yes_table_holds_on_the_command_line_for_every_role(tmp_path):
    env, log = _env(tmp_path)
    for argv, missing in (
        (["--role", "client"], "--server"),
        (["--role", "coordinator"], "--bind"),
        (["--role", "coordinator-worker", "--port", "18795"], "--bind"),
    ):
        done = _rcm(tmp_path, env, "setup", *argv, "--yes")
        assert done.returncode == 2, (argv, done.stderr)
        assert missing in done.stderr, done.stderr
    assert not log.exists()
    assert not (tmp_path / "config" / "rcm").exists()
    # client: --server 만 있으면 된다. 토큰은 코디네이터에서 만들어 RCM_TOKEN 으로 준다
    done = _rcm(tmp_path, env, "setup", "--role", "client", "--server", "http://h:18790", "--yes")
    assert done.returncode == 0, done.stderr
    assert (tmp_path / "config" / "rcm" / "client.toml").is_file()
    assert "rcm token add CLIENT_NAME" in done.stderr and "RCM_TOKEN" in done.stderr
    assert env["RCM_TOKEN"] not in done.stdout + done.stderr


def _coordinator(port: int, data: Path, *extra: str, role: str = "coordinator") -> list[str]:
    return [
        "setup",
        "--role",
        role,
        "--bind",
        "127.0.0.1",
        "--port",
        str(port),
        "--data-dir",
        str(data),
        "--label",
        "test.rcm.setup-cli-server",
        "--no-advertise",
        "--yes",
        *extra,
    ]


def test_tr31_rcm_setup_refuses_a_data_dir_with_a_database_and_reuse_reads_only_the_header(
    tmp_path,
):
    env, _ = _env(tmp_path)
    data = tmp_path / "old-data"
    data.mkdir()
    db = data / "rcm.sqlite3"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE t (x)")
    con.execute(f"PRAGMA user_version = {DB_VERSION - 1}")
    con.commit()
    con.close()
    before = db.read_bytes()
    port = _free_port()
    done = _rcm(tmp_path, env, *_coordinator(port, data))
    assert done.returncode == 1, done.stderr
    assert "already holds an rcm database" in done.stderr and "--reuse-data-dir" in done.stderr
    assert not (tmp_path / "config" / "rcm" / "server.toml").exists()
    done = _rcm(tmp_path, env, *_coordinator(port, data, "--reuse-data-dir"))
    assert done.returncode == 0, done.stderr
    server = tomllib.loads((tmp_path / "config" / "rcm" / "server.toml").read_text())["server"]
    assert server["data_dir"] == str(data)
    # 6-B-V3: 헤더의 판과 이 빌드의 판이 다르다고 알리고 DB 는 열지 않았다
    assert f"v{DB_VERSION - 1}" in done.stderr and f"v{DB_VERSION}" in done.stderr
    assert db.read_bytes() == before
    assert sorted(p.name for p in data.iterdir()) == ["rcm.sqlite3"]  # -wal · -shm 없음


def test_tr32_rcm_setup_warns_about_a_port_in_use(tmp_path):
    env, _ = _env(tmp_path)
    held = socket.socket()
    held.bind(("127.0.0.1", 0))
    held.listen(1)
    try:
        port = held.getsockname()[1]
        done = _rcm(tmp_path, env, *_coordinator(port, tmp_path / "srv-data"))
    finally:
        held.close()
    assert done.returncode == 0, done.stderr
    assert f"port {port} is in use" in done.stderr


def test_tr13_tr21_tr27_a_coordinator_setup_config_check_is_ok_and_prints_token_commands(
    tmp_path,
):
    """`lanes = 0` 을 dev 가 받는다(#271) — 마무리 진단의 설정 검증이 FAIL 이 아니라 ok 다."""
    env, _ = _env(tmp_path)
    done = _rcm(tmp_path, env, *_coordinator(_free_port(), tmp_path / "srv-data"))
    assert done.returncode == 0, done.stderr
    config = tmp_path / "config" / "rcm" / "server.toml"
    assert tomllib.loads(config.read_text())["server"]["lanes"] == 0
    rows = [line.split() for line in done.stderr.splitlines()]
    assert ["ok", "config:"] in [r[:2] for r in rows], done.stderr
    assert not any(r[:1] == ["FAIL"] for r in rows), done.stderr
    tokens = [line.strip() for line in done.stderr.splitlines() if " token --config " in line]
    assert any(t.endswith("add CLIENT_NAME") for t in tokens), tokens
    assert any(t.endswith("add WORKER_NAME --worker") for t in tokens), tokens
    assert all("--admin" not in t and str(config) in t for t in tokens), tokens


# ── 인자 없는 `rcm` 의 첫 실행 질문 (결정 240) ───────────────────────────────


QUESTION = "Set it up now?"


class _Stream(io.StringIO):
    """TTY 인 척하는(또는 아닌) 스트림."""

    def __init__(self, text: str = "", *, tty: bool = True):
        super().__init__(text)
        self.tty = tty

    def isatty(self) -> bool:
        return self.tty


@pytest.fixture
def bare(tmp_path, monkeypatch) -> Path:
    """설정 없는 머신 — 임시 HOME 과 XDG, 셸의 `RCM_*` 없음, 작업 폴더는 빈 임시 폴더."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    for name in ("RCM_CONFIG", "RCM_SERVER", "RCM_TOKEN", "RCM_WORKER_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return tmp_path


def bare_rcm(monkeypatch, *answers: str, stdin_tty=True, stderr_tty=True) -> tuple[int, str, str]:
    """`rcm` 을 인자 없이 부른다 — (종료 코드, stderr, 읽지 않고 남은 stdin)."""
    inp = _Stream("".join(a + "\n" for a in answers), tty=stdin_tty)
    err = _Stream(tty=stderr_tty)
    monkeypatch.setattr(sys, "stdin", inp)
    monkeypatch.setattr(sys, "stderr", err)
    try:
        code = cli.main([])
    except SystemExit as e:
        code = int(e.code or 0)
    return code, err.getvalue(), inp.read()


def test_tr3_a_terminal_with_no_config_asks_and_no_keeps_the_usage_error(bare, monkeypatch):
    code, err, _ = bare_rcm(monkeypatch, "n")
    assert QUESTION in err, err
    assert code == 2, err
    assert "usage" in err
    assert not (bare / "xdg" / "rcm").exists()


def test_tr3_yes_to_the_first_run_question_runs_setup_in_the_terminal(bare, monkeypatch):
    code, err, rest = bare_rcm(monkeypatch, "y", "client", "http://10.0.0.5:18790", "")
    assert QUESTION in err, err
    assert code == 0, err
    client = tomllib.loads((bare / "xdg" / "rcm" / "client.toml").read_text())
    assert client["server"] == "http://10.0.0.5:18790"
    assert rest == ""


def test_tr1_without_a_terminal_bare_rcm_never_asks(bare, monkeypatch):
    code, err, _ = bare_rcm(monkeypatch, "n")  # 대조: 둘 다 TTY 면 묻는다
    assert QUESTION in err
    for stdin_tty, stderr_tty in ((False, True), (True, False), (False, False)):
        code, err, rest = bare_rcm(monkeypatch, "y", stdin_tty=stdin_tty, stderr_tty=stderr_tty)
        assert code == 2, (stdin_tty, stderr_tty, err)
        assert QUESTION not in err, err
        assert rest == "y\n"  # stdin 을 읽지 않았다
    assert not (bare / "xdg" / "rcm").exists()


def test_tr2_a_terminal_with_any_config_does_not_ask(bare, monkeypatch):
    code, err, _ = bare_rcm(monkeypatch, "n")  # 대조: 아무 설정도 없으면 묻는다
    assert QUESTION in err
    cfg = bare / "xdg" / "rcm"
    cfg.mkdir(parents=True)
    for name, body in (
        ("server.toml", "[server]\nlanes = 0\n"),
        ("worker.toml", 'server = "http://h:1"\n'),
        ("client.toml", 'server = "http://h:1"\n'),
    ):
        (cfg / name).write_text(body, encoding="utf-8")
        code, err, rest = bare_rcm(monkeypatch, "y")
        assert code == 2 and QUESTION not in err, (name, err)
        assert rest == "y\n"
        (cfg / name).unlink()
    # ./rcm.toml 과 $RCM_CONFIG 도 서버 설정이다
    local = Path.cwd() / "rcm.toml"
    local.write_text("[server]\n", encoding="utf-8")
    code, err, _ = bare_rcm(monkeypatch, "y")
    assert code == 2 and QUESTION not in err, err
    local.unlink()
    elsewhere = bare / "elsewhere.toml"
    elsewhere.write_text("[server]\n", encoding="utf-8")
    monkeypatch.setenv("RCM_CONFIG", str(elsewhere))
    code, err, _ = bare_rcm(monkeypatch, "y")
    assert code == 2 and QUESTION not in err, err
    # TR38: $RCM_CONFIG 가 없는 파일이면 역할을 읽지 않고 사용법 오류다 — 묻지 않는다
    monkeypatch.setenv("RCM_CONFIG", str(bare / "gone.toml"))
    code, err, _ = bare_rcm(monkeypatch, "y")
    assert code == 2 and QUESTION not in err, err
    assert "RCM_CONFIG" in err


def test_tr19_an_env_only_client_counts_as_configured(bare, monkeypatch):
    code, err, _ = bare_rcm(monkeypatch, "n")  # 대조
    assert QUESTION in err
    for env in (
        {"RCM_SERVER": "http://h:1", "RCM_TOKEN": "tok-" + "M3w" * 6},
        {"RCM_SERVER": "http://h:1"},
        {"RCM_TOKEN": "tok-" + "M3w" * 6},
    ):
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        code, err, rest = bare_rcm(monkeypatch, "y")
        assert code == 2 and QUESTION not in err, (env, err)
        assert rest == "y\n"
        for k in env:
            monkeypatch.delenv(k)


def test_tr29_an_env_only_worker_counts_as_configured(bare, monkeypatch):
    code, err, _ = bare_rcm(monkeypatch, "n")  # 대조
    assert QUESTION in err
    monkeypatch.setenv("RCM_WORKER_TOKEN", "wtok-" + "P5q" * 6)
    code, err, rest = bare_rcm(monkeypatch, "y")
    assert code == 2 and QUESTION not in err, err
    assert rest == "y\n"


def test_tr30_with_xdg_the_first_run_check_uses_the_existing_search(bare, monkeypatch):
    legacy = bare / "home" / ".config" / "rcm"
    legacy.mkdir(parents=True)
    # server.toml · client.toml 은 XDG 쪽과 ~/.config/rcm 을 둘 다 본다
    for name, body in (
        ("server.toml", "[server]\n"),
        ("client.toml", 'server = "http://h:1"\n'),
    ):
        (legacy / name).write_text(body, encoding="utf-8")
        code, err, _ = bare_rcm(monkeypatch, "y")
        assert code == 2 and QUESTION not in err, (name, err)
        (legacy / name).unlink()
    # worker.toml 은 user_config_dir() 하나만 본다 — XDG 가 있으면 ~/.config/rcm 의 것은 없는 셈
    (legacy / "worker.toml").write_text('server = "http://h:1"\n', encoding="utf-8")
    code, err, _ = bare_rcm(monkeypatch, "n")
    assert QUESTION in err, err
    xdg = bare / "xdg" / "rcm"
    xdg.mkdir(parents=True)
    (xdg / "worker.toml").write_text('server = "http://h:1"\n', encoding="utf-8")
    code, err, _ = bare_rcm(monkeypatch, "y")
    assert code == 2 and QUESTION not in err, err


def _read_until(fd: int, want: bytes, seconds: float) -> bytes:
    buf = b""
    deadline = time.monotonic() + seconds
    while want not in buf and time.monotonic() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.2)
        if not ready:
            continue
        try:
            chunk = os.read(fd, 4096)
        except OSError:  # 자식이 터미널을 닫았다(macOS 는 EIO)
            break
        if not chunk:
            break
        buf += chunk
    return buf


def test_tr3_tr1_bare_rcm_asks_in_a_real_terminal_and_not_without_one(tmp_path):
    env, _ = _env(tmp_path)
    env.pop("RCM_TOKEN")
    main = "import sys; from remote_ci_monitor.cli import main; sys.exit(main())"
    master, slave = pty.openpty()
    try:
        proc = subprocess.Popen(
            [sys.executable, "-c", main],
            stdin=slave,
            stdout=subprocess.DEVNULL,
            stderr=slave,
            env=env,
            cwd=tmp_path,
        )
        os.close(slave)
        seen = _read_until(master, QUESTION.encode(), 30)
        if QUESTION.encode() in seen:
            os.write(master, b"n\n")
        proc.wait(timeout=30)
        seen += _read_until(master, b"\x00", 1)
    finally:
        os.close(master)
    assert QUESTION.encode() in seen, seen
    assert proc.returncode == 2
    assert not (tmp_path / "config" / "rcm").exists()
    done = subprocess.run(
        [sys.executable, "-c", main],
        stdin=subprocess.DEVNULL,
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 2
    assert QUESTION not in done.stderr and "usage" in done.stderr


def test_b10_m6_a_subprocess_setup_scans_the_swapped_system_places(tmp_path):
    # batch 10 M6: 다른 프로세스의 setup 은 sitecustomize 가 바꿔 끼운 시스템 자리를 본다 — 그
    # 자리의 읽지 못하는 rcm 서비스가 setup 을 멈춘다(진짜 `/Library/LaunchDaemons` 는 읽지 않는다)
    env, _ = _env(tmp_path)
    daemons = tmp_path / "sys" / "LaunchDaemons"
    daemons.mkdir(parents=True)
    plist = daemons / "com.remote-ci-monitor.server.plist"
    plist.write_bytes(b"<plist><dict><key>Label")
    args = ["--role", "coordinator-worker", "--bind", "127.0.0.1", "--port", str(_free_port())]
    args += ["--data-dir", str(tmp_path / "d"), "--label", LABEL, "--no-advertise", "--yes"]
    done = _setup(tmp_path, env, *args)
    assert done.returncode == 1, done.stderr
    assert str(plist) in done.stderr and "refusing" in done.stderr, done.stderr
