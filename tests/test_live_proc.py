"""`tools/live_proc.py` — 시험 서버의 프로세스를 띄우고, 띄울 때 적은 신원에만 신호를 보낸다.

`.claude/skills/_shared/test-server.md` 「Signals」 가 이 도구를 부른다(2026-11-27). 셸 블록으로
띄우던 꼴(`… & echo $! > pid`)은 빌드 머신의 운영 가드 훅에 막혀 그대로 따라 할 수 없었다.
지키는 것:

- 신원은 pid 와 띄운 직후 적은 시작 시각(`LC_ALL=C TZ=UTC ps -o lstart=`)이다. 지금 `ps` 의
  시작 시각이 다르면(pid 재사용) · pid 파일이 없거나 비었거나 1 이하면 · `--expect-config` 의
  경로가 명령줄에 없으면 아무것도 보내지 않고 이유를 찍고 `BLOCKED`, 종료 3.
- `--interactive-sigint` 로 띄운 파이썬은 SIGINT 로 끝난다. 작업 제어 없는 셸이 뒤로 보낸 것처럼
  SIGINT 를 무시한 채 띄우면(플래그 없음) 끝나지 않는다 — 대조.
- `stop-all` 은 그 폴더에 적힌 것만 거둔다. 적히지 않은 프로세스, 시작 시각이 다른 프로세스는
  살아 있다.
- 운영 경로(`~/.local/share/rcm` · `~/.config/rcm` · `/tmp`, 운영 설정 · 포트 8787)는 띄우기 전에
  거부한다(종료 4). 운영 가드 훅의 판정(`guard_production.decide`)도 안쪽 명령에 그대로 묻는다.

시험이 띄우는 자식은 이 pytest 의 자식이라 끝나면 좀비로 남는다. 그래서 도구는 좀비를 「끝남」
으로 보고, 시험은 `Popen.wait` 로 거둔다. 시간은 짧게 — 신호가 간 쪽은 5초 안에 끝나고, 안 간
쪽은 0.5초 기다려 본다.
"""

from __future__ import annotations

import importlib.util
import io
import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    path = ROOT / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gp = _load("guard_production")
lp = _load("live_proc")

SLEEPER = [sys.executable, "-c", "import time; time.sleep(30)"]
#: 준비를 알리고 자는 파이썬 — 신호 처분을 다 정한 뒤 「ready」를 찍는다. 시험은 그 줄을 보고
#: 신호를 보낸다(파이썬이 처리기를 달기 전에 닿는 경주를 없앤다).
READY = "print('ready', flush=True); time.sleep(30)"
OLD_START = "Thu Jan  1 00:00:00 1970\n"


def _ps(pid: int, field: str) -> str:
    env = dict(os.environ, LC_ALL="C", TZ="UTC")
    out = subprocess.run(
        ["ps", "-p", str(pid), "-o", f"{field}="], env=env, capture_output=True, text=True
    )
    return out.stdout


def _places(home: Path, *, tmp_roots: tuple[Path, ...] = ()) -> object:
    return lp.Places(home=home, prod=gp.Production(), tmp_roots=tmp_roots)


@pytest.fixture
def d(tmp_path: Path) -> Path:
    # 시험 서버 폴더: 도구는 안쪽 명령의 `--config` 를 읽어 보므로 그 파일들을 둔다.
    path = tmp_path / "home" / ".local" / "share" / "rcm-devtest-t"
    path.mkdir(parents=True)
    _test_configs(path)
    return path


def _test_configs(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    server = f'[server]\nport = 8790\ndata_dir = "{folder}"\nadvertise = false\n'
    worker = f'server = "http://127.0.0.1:8790"\ndata_dir = "{folder}/worker"\n'
    for name, text in (("server.toml", server), ("worker.toml", worker)):
        (folder / name).write_text(text, encoding="utf-8")
    (folder / "worker.toml.bak").write_text(worker, encoding="utf-8")


@pytest.fixture
def procs() -> Iterator[list[subprocess.Popen]]:
    started: list[subprocess.Popen] = []
    yield started
    for proc in started:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)


def _start(d: Path, name: str, argv: list[str], procs: list, **kw) -> subprocess.Popen:
    out = io.StringIO()
    proc = lp.start(d, name, argv, places=_places(d.parents[2]), out=out, **kw)
    assert isinstance(proc, subprocess.Popen), out.getvalue()
    procs.append(proc)
    return proc


def _wait_ready(d: Path, name: str) -> None:
    log = d / f"{name}.log"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if "ready" in log.read_text(encoding="utf-8"):
            return
        time.sleep(0.05)
    raise AssertionError(f"{name} never printed ready: {log.read_text(encoding='utf-8')!r}")


def _rc(proc: subprocess.Popen, timeout: float) -> int | None:
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return None


def _send(d: Path, name: str, sig: str = "TERM", **kw) -> tuple[int, list[str]]:
    out = io.StringIO()
    code = lp.send(d, name, sig, out=out, **kw)
    return code, out.getvalue().splitlines()


def _assert_blocked(code: int, lines: list[str]) -> None:
    assert code == 3, lines
    assert lines and lines[-1] == "BLOCKED", lines
    for head in ("recorded start:", "current start:", "command:"):
        assert any(line.startswith(head) for line in lines), (head, lines)


# --- start ------------------------------------------------------------------


def test_start_records_the_pid_then_the_start_time_and_logs(d: Path, procs: list) -> None:
    proc = _start(d, "worker", [*SLEEPER, "--config", f"{d}/worker.toml"], procs)
    assert (d / "worker.pid").read_text(encoding="utf-8").strip() == str(proc.pid)
    recorded = (d / "worker.start").read_text(encoding="utf-8")
    assert recorded.strip() and recorded.strip() == _ps(proc.pid, "lstart").strip()
    assert (d / "worker.log").exists()
    # 셸을 거치지 않는다 — argv 가 그대로 명령줄이다.
    assert f"--config {d}/worker.toml" in _ps(proc.pid, "command")
    assert not list(d.glob("*.tmp")) and not list(d.glob(".*.tmp"))


def test_start_sends_output_to_the_log(d: Path, procs: list) -> None:
    argv = [
        sys.executable,
        "-c",
        "import sys; print('out-line'); print('err-line', file=sys.stderr)",
    ]
    proc = _start(d, "client", argv, procs)
    assert proc.wait(timeout=10) == 0
    log = (d / "client.log").read_text(encoding="utf-8")
    assert "out-line" in log and "err-line" in log


# --- signal -----------------------------------------------------------------


@pytest.mark.parametrize("name", ["server", "worker", "client"])
def test_signal_reaches_the_recorded_process(d: Path, procs: list, name: str) -> None:
    argv = SLEEPER if name == "client" else [*SLEEPER, "--config", f"{d}/{name}.toml"]
    proc = _start(d, name, argv, procs)
    expect = None if name == "client" else f"{d}/{name}.toml"
    code, lines = _send(d, name, "TERM", expect_config=expect)
    assert code == 0, lines
    assert _rc(proc, 5) == -signal.SIGTERM, lines
    assert "BLOCKED" not in lines


def test_a_changed_start_time_is_another_process(d: Path, procs: list) -> None:
    # pid 재사용 흉내: 적힌 pid 는 살아 있지만 시작 시각이 다르다 — 이제 남의 것이다.
    proc = _start(d, "worker", [*SLEEPER, "--config", f"{d}/worker.toml"], procs)
    (d / "worker.start").write_text(OLD_START, encoding="utf-8")
    code, lines = _send(d, "worker", "KILL", expect_config=f"{d}/worker.toml")
    _assert_blocked(code, lines)
    assert any("1970" in line for line in lines if line.startswith("recorded start:")), lines
    assert _rc(proc, 0.5) is None, "a process with another start time got the signal"


@pytest.mark.parametrize("content", [None, "", "   \n", "1\n", "0\n", "-7\n", "abc\n"])
def test_a_missing_empty_or_low_pid_is_refused(
    d: Path, procs: list, content: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    proc = _start(d, "worker", [*SLEEPER, "--config", f"{d}/worker.toml"], procs)
    sent: list[tuple[int, int]] = []
    if content is None:
        (d / "worker.pid").unlink()
    else:
        (d / "worker.pid").write_text(content, encoding="utf-8")
    # pid 0 은 자기 프로세스 그룹이다 — 잘못 보내면 pytest 가 죽으니 kill 을 가로챈다.
    with monkeypatch.context() as m:
        m.setattr(lp.os, "kill", lambda pid, sig: sent.append((pid, sig)))
        code, lines = _send(d, "worker", "TERM", expect_config=f"{d}/worker.toml")
    _assert_blocked(code, lines)
    assert sent == [], "kill was called"
    assert proc.poll() is None


def test_an_empty_start_file_is_refused(d: Path, procs: list) -> None:
    proc = _start(d, "worker", [*SLEEPER, "--config", f"{d}/worker.toml"], procs)
    (d / "worker.start").write_text("", encoding="utf-8")
    code, lines = _send(d, "worker", "TERM", expect_config=f"{d}/worker.toml")
    _assert_blocked(code, lines)
    assert _rc(proc, 0.5) is None


@pytest.mark.parametrize("elsewhere", [True, False])
def test_expect_config_must_be_on_the_command_line(
    tmp_path: Path, d: Path, procs: list, elsewhere: bool
) -> None:
    # 시작 시각은 맞아도 명령줄에 이 data_dir 의 설정이 없다(운영 워커일 수 있다).
    _test_configs(tmp_path / "elsewhere")
    config = ["--config", f"{tmp_path}/elsewhere/worker.toml"] if elsewhere else []
    proc = _start(d, "worker", [*SLEEPER, *config], procs)
    code, lines = _send(d, "worker", "TERM", expect_config=f"{d}/worker.toml")
    _assert_blocked(code, lines)
    assert any(line.startswith("command:") and "time.sleep" in line for line in lines), lines
    assert _rc(proc, 0.5) is None


def test_expect_config_is_a_whole_word_not_a_prefix(d: Path, procs: list) -> None:
    proc = _start(d, "worker", [*SLEEPER, "--config", f"{d}/worker.toml.bak"], procs)
    code, lines = _send(d, "worker", "TERM", expect_config=f"{d}/worker.toml")
    _assert_blocked(code, lines)
    assert _rc(proc, 0.5) is None


def test_a_gone_process_is_blocked_not_signalled(d: Path, procs: list) -> None:
    proc = _start(d, "client", [sys.executable, "-c", "pass"], procs)
    assert proc.wait(timeout=10) == 0
    code, lines = _send(d, "client", "INT")
    _assert_blocked(code, lines)


# --- SIGINT -----------------------------------------------------------------


def _start_ignoring_sigint(d: Path, procs: list, **kw) -> subprocess.Popen:
    # 작업 제어 없는 셸이 뒤로 보낸 것과 같은 조건: 띄우는 쪽이 SIGINT 를 무시하고 있다.
    old = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        proc = _start(d, "client", [sys.executable, "-c", f"import time; {READY}"], procs, **kw)
    finally:
        signal.signal(signal.SIGINT, old)
    _wait_ready(d, "client")
    return proc


def test_interactive_sigint_lets_python_end_on_ctrl_c(d: Path, procs: list) -> None:
    proc = _start_ignoring_sigint(d, procs, interactive_sigint=True)
    code, lines = _send(d, "client", "INT")
    assert code == 0, lines
    assert _rc(proc, 5) == -signal.SIGINT


def test_without_interactive_sigint_the_ignored_sigint_stays_ignored(d: Path, procs: list) -> None:
    # 대조: 플래그가 없으면 물려받은 SIG_IGN 그대로라 SIGINT 가 닿아도 끝나지 않는다.
    proc = _start_ignoring_sigint(d, procs)
    code, lines = _send(d, "client", "INT")
    assert code == 0, lines
    assert _rc(proc, 0.5) is None


# --- stop-all ---------------------------------------------------------------


def test_stop_all_reaps_only_what_this_folder_recorded(d: Path, procs: list) -> None:
    server = _start(d, "server", [*SLEEPER, "--config", f"{d}/server.toml"], procs)
    # SIGTERM 을 무시하는 워커 — 기다린 뒤 SIGKILL 로 거둔다.
    stubborn = f"import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); {READY}"
    worker = _start(d, "worker", [sys.executable, "-c", stubborn], procs)
    _wait_ready(d, "worker")
    other = subprocess.Popen(SLEEPER)  # 적히지 않은 프로세스
    procs.append(other)
    reused = _start(d, "client", SLEEPER, procs)
    (d / "client.start").write_text(OLD_START, encoding="utf-8")  # 시작 시각이 다르다
    out = io.StringIO()
    code = lp.stop_all(d, grace=1.0, out=out)
    lines = out.getvalue().splitlines()
    assert code == 3, lines  # client 는 BLOCKED
    assert _rc(server, 5) == -signal.SIGTERM, lines
    assert _rc(worker, 5) == -signal.SIGKILL, lines
    assert other.poll() is None, "stop-all reached a process it never recorded"
    assert _rc(reused, 0.5) is None, "stop-all signalled a pid whose start time differs"
    assert any("BLOCKED" in line and "client" in line for line in lines), lines


def test_stop_all_on_an_empty_folder_does_nothing(d: Path) -> None:
    out = io.StringIO()
    assert lp.stop_all(d, grace=0.1, out=out) == 0


# --- 운영 가드 --------------------------------------------------------------


def _refusal(home: Path, directory: Path, argv: list[str], **kw) -> str | None:
    environ = kw.pop("environ", {})
    places = lp.Places(
        home=home, prod=kw.pop("prod", gp.Production()), tmp_roots=kw.pop("tmp_roots", ())
    )
    return lp.refusal(directory, argv, places, cwd=directory, environ=environ)


@pytest.mark.parametrize(
    "where",
    [".local/share/rcm", ".local/share/rcm/jobs", ".config/rcm", ".config/rcm/secrets"],
)
def test_production_folders_are_refused(tmp_path: Path, where: str) -> None:
    home = tmp_path / "home"
    target = home / where
    target.mkdir(parents=True)
    assert _refusal(home, target, SLEEPER)


@pytest.mark.parametrize("root", ["/tmp", "/private/tmp"])
def test_tmp_is_refused(tmp_path: Path, root: str) -> None:
    home = tmp_path / "home"
    target = Path(root) / "rcm-devtest-live-proc-never-created"
    assert _refusal(home, target, SLEEPER, tmp_roots=(Path("/tmp"), Path("/private/tmp")))
    assert not target.exists()


def test_a_test_folder_is_allowed(tmp_path: Path, d: Path) -> None:
    (d / "server.toml").write_text(
        f'[server]\nport = 8790\ndata_dir = "{d}"\nadvertise = false\n', encoding="utf-8"
    )
    argv = ["./.venv/bin/rcm", "serve", "--config", f"{d}/server.toml"]
    assert _refusal(tmp_path / "home", d, argv) is None
    assert _refusal(tmp_path / "home", d, SLEEPER) is None


def test_the_production_config_is_refused(tmp_path: Path, d: Path) -> None:
    home = tmp_path / "home"
    conf = home / ".config" / "rcm"
    conf.mkdir(parents=True)
    argv = ["./.venv/bin/rcm", "serve", "--config", f"{conf}/server.toml"]
    assert _refusal(home, d, argv)
    assert _refusal(home, d, ["./.venv/bin/rcm", "serve", f"--config={conf}/server.toml"])


@pytest.mark.parametrize(
    "toml",
    [
        '[server]\nport = 8787\ndata_dir = "{d}"\n',  # 운영 포트
        '[server]\nport = 8790\ndata_dir = "{home}/.local/share/rcm"\n',  # 운영 data_dir 사본
        'server = "http://127.0.0.1:8787"\ndata_dir = "{d}/worker"\n',  # 운영 서버의 워커
        "[server\n",  # 못 읽는 설정 — 무엇을 열지 모르면 띄우지 않는다
    ],
)
def test_a_config_that_reaches_production_is_refused(tmp_path: Path, d: Path, toml: str) -> None:
    home = tmp_path / "home"
    (d / "x.toml").write_text(toml.format(d=d, home=home), encoding="utf-8")
    assert _refusal(home, d, ["./.venv/bin/rcm", "worker", "--config", f"{d}/x.toml"])


@pytest.mark.parametrize("sub", ["serve", "worker", "token"])
def test_rcm_without_an_explicit_config_is_refused(tmp_path: Path, d: Path, sub: str) -> None:
    assert _refusal(tmp_path / "home", d, ["./.venv/bin/rcm", sub])


def test_a_client_pointed_at_port_8787_is_refused(tmp_path: Path, d: Path) -> None:
    argv = ["./.venv/bin/rcm", "run", "slow", "--by", "acceptance"]
    environ = {"RCM_SERVER": "http://127.0.0.1:8787"}
    assert _refusal(tmp_path / "home", d, argv, environ=environ)
    assert (
        _refusal(tmp_path / "home", d, argv, environ={"RCM_SERVER": "http://127.0.0.1:8790"})
        is None
    )


def test_the_guard_hook_is_asked_about_the_inner_command(tmp_path: Path, d: Path) -> None:
    # 운영 가드 훅의 판정을 안쪽 명령에 그대로 묻는다 — 도구가 훅을 돌아가는 길이 되지 않게.
    prod_data = tmp_path / "prod-data"
    prod_data.mkdir()
    prod = gp.Production(data_dir=prod_data)
    argv = [
        "./.venv/bin/rcm",
        "serve",
        "--config",
        f"{d}/server.toml",
        "--data-dir",
        str(prod_data),
    ]
    (d / "server.toml").write_text(f'[server]\nport = 8790\ndata_dir = "{d}"\n', encoding="utf-8")
    assert _refusal(tmp_path / "home", d, argv, prod=prod)
    # 운영 뿌리 안의 폴더도.
    inside_prod = prod_data / "x"
    inside_prod.mkdir()
    assert _refusal(tmp_path / "home", inside_prod, SLEEPER, prod=prod)


def test_a_refused_start_starts_nothing(tmp_path: Path, d: Path) -> None:
    out = io.StringIO()
    code = lp.start(
        d, "server", ["./.venv/bin/rcm", "serve"], places=_places(tmp_path / "home"), out=out
    )
    assert code == 4, out.getvalue()
    assert "REFUSED" in out.getvalue()
    assert not (d / "server.pid").exists() and not (d / "server.log").exists()


def test_the_cli_refuses_tmp_and_the_real_production_folder() -> None:
    # 이 머신의 진짜 홈으로 — 거부는 무엇을 만들기 전에 난다.
    real = Path.home() / ".local" / "share" / "rcm"
    for target in (Path("/tmp/rcm-devtest-live-proc-never-created"), real):
        before = target.exists()
        out = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "live_proc.py"), "start", "--dir", str(target),
             "--name", "server", "--", *SLEEPER],
            capture_output=True, text=True, timeout=20,
        )  # fmt: skip
        assert out.returncode == 4, (target, out.stdout, out.stderr)
        assert "REFUSED" in out.stdout + out.stderr
        assert target.exists() is before
        if before:
            assert not (target / "server.pid").exists()


def test_the_cli_signal_blocks_without_a_pid_file(d: Path) -> None:
    out = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "live_proc.py"), "signal", "--dir", str(d),
         "--name", "worker", "--sig", "TERM"],
        capture_output=True, text=True, timeout=20,
    )  # fmt: skip
    assert out.returncode == 3, (out.stdout, out.stderr)
    assert out.stdout.splitlines()[-1] == "BLOCKED"


def test_the_cli_has_no_lookup_by_name() -> None:
    out = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "live_proc.py"), "--help"],
        capture_output=True, text=True, timeout=20,
    )  # fmt: skip
    assert out.returncode == 0
    for word in ("start", "signal", "stop-all"):
        assert word in out.stdout
    assert "pkill" not in out.stdout and "pgrep" not in out.stdout
