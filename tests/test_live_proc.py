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
import shutil
import signal
import subprocess
import sys
import tempfile
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
def outside_tmp() -> Iterator[Path]:
    """`/tmp` 밖의 시험 폴더 하나. 맥미니 CI 는 TMPDIR 을 /private/tmp 아래에 두어 tmp_path 도
    거기 있고, 도구는 /tmp 아래의 `--dir` 를 거부한다(잡 4380). 그래서 진짜 홈의
    `~/.local/share/rcm-devtest-livep-<무작위>` 를 만들고 끝나면 그 폴더만 지운다 — 시험 서버
    문서의 자리와 같은 꼴이고, 운영 자리(`~/.local/share/rcm`)의 형제일 뿐 안이 아니다."""
    base = lp._real_home() / ".local" / "share"
    base.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="rcm-devtest-livep-", dir=base))
    try:
        yield folder
    finally:
        shutil.rmtree(folder, ignore_errors=True)


@pytest.fixture
def d(outside_tmp: Path) -> Path:
    # 시험 서버 폴더: 도구는 안쪽 명령의 `--config` 를 읽어 보므로 그 파일들을 둔다.
    path = outside_tmp / "home" / ".local" / "share" / "rcm-devtest-t"
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


# --- batch-6: 격리 확인의 지적 ---------------------------------------------------
# 사례마다 `# B6-<n>` 은 batch-6 in 파일의 항목 번호다. 도구 자체 검사와 `gp.decide` 가 겹치는
# 사례는 어느 한쪽을 지워도 초록이라 지킴을 잠그지 못한다 — 그래서 한쪽만 잡는 사례를 따로 둔다.


def _good_server(d: Path) -> list[str]:
    return ["./.venv/bin/rcm", "serve", "--config", f"{d}/server.toml"]


def _no_ps(tmp_path: Path) -> str:
    return str(tmp_path / "no-such-ps")


@pytest.mark.parametrize(
    "extra",
    [
        ["--port", "8787"],
        ["--port=8787"],
        ["--port", "08787"],  # argparse 의 type=int 는 int() 그대로 — 앞의 0 도 8787 이다
        ["--bind", "127.0.0.1:8787"],
        ["--server", "http://127.0.0.1:8787"],
    ],
)
def test_b6_1_port_8787_on_the_command_line_is_refused(
    tmp_path: Path, d: Path, extra: list[str]
) -> None:
    # B6-1 — CLI 의 `--port` 는 설정 위에 얹힌다. 설정이 8790 이어도 `--port 8787` 이면 운영 포트다.
    reason = _refusal(tmp_path / "home", d, [*_good_server(d), *extra])
    assert reason and "8787" in reason, reason


def test_b6_1_another_port_on_the_command_line_is_allowed(tmp_path: Path, d: Path) -> None:
    # B6-1 — 대조: 운영이 아닌 포트는 그대로 띄운다.
    assert _refusal(tmp_path / "home", d, [*_good_server(d), "--port", "8791"]) is None


@pytest.mark.parametrize("toml", ["", 'title = "no server table"\n', "[other]\nport = 8790\n"])
def test_b6_2_a_config_without_a_server_table_is_port_8787(
    tmp_path: Path, d: Path, toml: str
) -> None:
    # B6-2 — `[server]` 가 없으면 `ServerConfig.port` 의 기본값 8787 로 뜬다.
    (d / "bare.toml").write_text(toml, encoding="utf-8")
    argv = ["./.venv/bin/rcm", "serve", "--config", f"{d}/bare.toml"]
    reason = _refusal(tmp_path / "home", d, argv)
    assert reason and "8787" in reason, reason


@pytest.mark.parametrize(
    "extra",
    [
        ["--por", "8787"],
        ["--po=8787"],
        ["--serv", "http://127.0.0.1:8787"],
        ["--data-d", "{home}/.local/share/rcm"],
        ["--data-={home}/.local/share/rcm"],
        ["--conf", "{home}/.config/rcm/server.toml"],
        ["--confi={home}/.config/rcm/server.toml"],
    ],
)
def test_b6_3_an_abbreviated_option_is_read_as_the_whole_option(
    tmp_path: Path, d: Path, extra: list[str]
) -> None:
    # B6-3 — rcm 의 argparse 는 `allow_abbrev` 를 끄지 않아 `--por 8787` 이 `--port 8787` 이다.
    home = tmp_path / "home"
    (home / ".config" / "rcm").mkdir(parents=True)
    words = [word.format(home=home) for word in extra]
    argv = ["./.venv/bin/rcm", "serve", "--config", f"{d}/server.toml", *words]
    assert _refusal(home, d, argv), argv


def test_b6_3_an_abbreviated_option_with_a_test_value_is_allowed(tmp_path: Path, d: Path) -> None:
    # B6-3 — 대조: 줄임말을 거부하는 것이 아니라 그 값을 본다.
    assert _refusal(tmp_path / "home", d, [*_good_server(d), "--por", "8791"]) is None


def test_b6_4_a_failing_ps_blocks_signal(tmp_path: Path, d: Path, procs: list) -> None:
    # B6-4 — `ps` 를 못 돌리면 그 프로세스가 끝났는지 모른다. 「not running」이 아니라 ps 가 이유다.
    proc = _start(d, "worker", [*SLEEPER, "--config", f"{d}/worker.toml"], procs)
    with pytest.MonkeyPatch.context() as m:
        m.setattr(lp, "PS_COMMAND", _no_ps(tmp_path), raising=False)
        code, lines = _send(d, "worker", "TERM", expect_config=f"{d}/worker.toml")
    _assert_blocked(code, lines)
    reasons = [line for line in lines if line.startswith("reason:")]
    assert reasons and "ps" in reasons[0] and "not running" not in reasons[0], lines
    assert proc.poll() is None


def test_b6_4_a_ps_that_hangs_blocks_stop_all(tmp_path: Path, d: Path, procs: list) -> None:
    # B6-4 — 시간 초과도 같다: 아무것도 보내지 않고 BLOCKED, 종료 3.
    proc = _start(d, "server", [*SLEEPER, "--config", f"{d}/server.toml"], procs)
    slow = tmp_path / "slow-ps"
    slow.write_text("#!/bin/sh\nsleep 5\n", encoding="utf-8")
    slow.chmod(0o755)
    out = io.StringIO()
    with pytest.MonkeyPatch.context() as m:
        m.setattr(lp, "PS_COMMAND", str(slow), raising=False)
        m.setattr(lp, "PS_TIMEOUT", 0.2, raising=False)
        code = lp.stop_all(d, grace=0.2, out=out)
    lines = out.getvalue().splitlines()
    assert code == 3, lines
    assert any("server" in line and "BLOCKED" in line for line in lines), lines
    assert not any("not running" in line or "stopped by" in line for line in lines), lines
    assert proc.poll() is None, "stop-all signalled a process it could not confirm"


def test_b6_4_ps_failing_after_sigterm_keeps_the_process_pending(
    tmp_path: Path, d: Path, procs: list
) -> None:
    # B6-4 — SIGTERM 뒤 기다리는 동안 `ps` 가 죽으면 「stopped by SIGTERM」이 아니다.
    # 확인 못 한 것은 남겨 두고(SIGKILL 도 보내지 않는다) BLOCKED 로 끝낸다.
    stubborn = f"import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); {READY}"
    worker = _start(d, "worker", [sys.executable, "-c", stubborn], procs)
    _wait_ready(d, "worker")
    real_kill = os.kill
    sent: list[int] = []
    out = io.StringIO()
    with pytest.MonkeyPatch.context() as m:

        def kill(pid: int, sig: int) -> None:
            sent.append(sig)
            real_kill(pid, sig)
            m.setattr(lp, "PS_COMMAND", _no_ps(tmp_path), raising=False)

        m.setattr(lp.os, "kill", kill)
        code = lp.stop_all(d, grace=0.5, out=out)
    lines = out.getvalue().splitlines()
    assert code == 3, lines
    assert not any("stopped by SIGTERM" in line for line in lines), lines
    assert any("worker" in line and "BLOCKED" in line for line in lines), lines
    assert sent == [signal.SIGTERM], sent
    assert worker.poll() is None


@pytest.mark.parametrize(
    "argv",
    [
        ["rm", "-rf", "{checkout}/src"],
        ["git", "-C", "{checkout}", "switch", "-c", "elsewhere"],
    ],
)
def test_b6_5_only_the_guard_hook_refuses_these(tmp_path: Path, d: Path, argv: list[str]) -> None:
    # B6-5 — 도구 자체 검사는 `--dir` · 설정 · 포트만 본다. 운영 체크아웃을 지우거나 옮기는 명령은
    # 훅의 판정(`gp.decide`)만 막는다 — 그 호출을 지우면 이 사례가 빨개진다.
    checkout = tmp_path / "prod-checkout"
    (checkout / "src").mkdir(parents=True)
    prod = gp.Production(checkout=checkout)
    words = [word.format(checkout=checkout) for word in argv]
    reason = _refusal(tmp_path / "home", d, words, prod=prod)
    assert reason and "guard_production" in reason, reason


def test_b6_5_the_guard_hook_is_called_with_the_inner_command(
    tmp_path: Path, d: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # B6-5 — `gp.decide` 가 불리지 않으면 빨개진다: 받은 명령 글과 그 판정이 거부 이유가 된다.
    calls: list[tuple[str, dict]] = []

    def decide(tool: str, payload: dict, *args: object, **kw: object) -> object:
        calls.append((tool, payload))
        return gp.Verdict("deny", "sentinel-from-the-hook")

    monkeypatch.setattr(lp.gp, "decide", decide)
    argv = [*SLEEPER, "a b"]
    reason = _refusal(tmp_path / "home", d, argv)
    assert calls == [("Bash", {"command": lp.shlex.join(argv)})], calls
    assert reason and "sentinel-from-the-hook" in reason, reason


@pytest.mark.parametrize(
    ("argv", "environ"),
    [
        (["./.venv/bin/rcm", "serve", "--config", "{d}/server.toml"],
         {"RCM_SERVER_DATA_DIR": "{home}/.local/share/rcm"}),
        (["./.venv/bin/rcm", "serve", "--config", "{d}/server.toml"], {"RCM_SERVER_PORT": "8787"}),
        (["./.venv/bin/rcm", "serve"], {"RCM_CONFIG": "{home}/.config/rcm/server.toml"}),
        (["./.venv/bin/rcm", "run", "slow", "--by", "acceptance"], {}),
        (["./.venv/bin/rcm", "status"], {"RCM_TOKEN": "x"}),
    ],
)  # fmt: skip
def test_b6_5_environment_cases_are_refused(
    tmp_path: Path, d: Path, argv: list[str], environ: dict[str, str]
) -> None:
    # B6-5 — 환경으로 운영에 닿는 꼴: 운영 data_dir · 운영 포트 · 운영 설정,
    # 서버 주소 없는 클라이언트.
    home = tmp_path / "home"
    conf = home / ".config" / "rcm"
    conf.mkdir(parents=True)
    (conf / "server.toml").write_text(f'[server]\nport = 8790\ndata_dir = "{d}"\n', "utf-8")
    words = [word.format(d=d) for word in argv]
    env = {key: value.format(home=home) for key, value in environ.items()}
    assert _refusal(home, d, words, environ=env), (words, env)


def test_b6_5_a_test_config_through_rcm_config_is_allowed(tmp_path: Path, d: Path) -> None:
    # B6-5 — 대조: `RCM_CONFIG` 가 시험 설정이면 `--config` 없는 `rcm serve` 도 띄운다.
    environ = {"RCM_CONFIG": f"{d}/server.toml"}
    assert _refusal(tmp_path / "home", d, ["./.venv/bin/rcm", "serve"], environ=environ) is None


def test_b6_5_a_readable_production_config_is_refused_for_its_place(
    tmp_path: Path, d: Path
) -> None:
    # B6-5 — 운영 설정 폴더의 파일은 포트 · data_dir 가 시험 값이어도 거부한다
    # (읽을 수 없어서가 아니라 그 자리여서).
    home = tmp_path / "home"
    conf = home / ".config" / "rcm"
    conf.mkdir(parents=True)
    (conf / "server.toml").write_text(f'[server]\nport = 8790\ndata_dir = "{d}"\n', "utf-8")
    reason = _refusal(home, d, ["./.venv/bin/rcm", "serve", "--config", f"{conf}/server.toml"])
    assert reason and "inside" in reason and str(conf) in reason, reason


def test_b6_5_pid_one_is_refused_even_with_its_own_start_time(
    d: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # B6-5 — pid 1 의 진짜 시작 시각을 적어도 보내지 않는다
    # (시작 시각 검사만으로는 이 사례를 못 막는다).
    (d / "worker.pid").write_text("1\n", encoding="utf-8")
    (d / "worker.start").write_text(_ps(1, "lstart"), encoding="utf-8")
    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(lp.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    code, lines = _send(d, "worker", "TERM")
    _assert_blocked(code, lines)
    out = io.StringIO()
    assert lp.stop_all(d, grace=0.1, out=out) == 3, out.getvalue()
    assert sent == []


def test_b6_5_an_unreaped_child_is_a_zombie_and_counts_as_gone(
    d: Path, procs: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    # B6-5 — 부모(이 pytest)가 거두지 않은 자식은 좀비로 남고 `ps` 는 그 시작 시각을 그대로 찍는다.
    # 도구는 Z 갈래로 「끝남」을 본다 — 좀비에 신호를 보내 「보냈다」고 하지 않는다.
    proc = _start(d, "client", [sys.executable, "-c", "pass"], procs)
    deadline = time.monotonic() + 10
    while not _ps(proc.pid, "stat").strip().startswith("Z"):
        assert time.monotonic() < deadline, "the child never became a zombie"
        time.sleep(0.05)
    sent: list[tuple[int, int]] = []
    with monkeypatch.context() as m:
        m.setattr(lp.os, "kill", lambda pid, sig: sent.append((pid, sig)))
        code, lines = _send(d, "client", "INT")
        out = io.StringIO()
        all_code = lp.stop_all(d, grace=0.1, out=out)
    _assert_blocked(code, lines)
    assert any("not running" in line for line in lines), lines
    assert all_code == 0 and "client: not running" in out.getvalue(), out.getvalue()
    assert sent == []


def test_b6_7_a_child_that_ends_at_once_is_exit_1_with_its_log(
    d: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # B6-7 — 곧바로 죽은 자식을 `started` · 종료 0 으로 말하지 않는다: 종료 1 과 로그 꼬리.
    # B7-5 — 1초(SETTLE_SECONDS) 안에 죽기를 바라지 않는다: 부하에서는 파이썬이 뜨는 데만 1초를
    # 넘긴다. 지켜보는 창을 넉넉히 늘려 자식이 반드시 그 안에서 끝나게 하고(도구는 끝나는 즉시
    # 판정한다), 판정은 그 뒤에 본다. 같은 CLI 길(`main`)을 프로세스 안에서 부른다.
    monkeypatch.setattr(lp, "SETTLE_SECONDS", 30.0)
    program = "import sys; print('boom-from-the-child', flush=True); sys.exit(5)"
    out = io.StringIO()
    argv = ["start", "--dir", str(d), "--name", "client", "--", sys.executable, "-c", program]
    began = time.monotonic()
    code = lp.main(argv, places=_places(d.parents[2]), out=out)
    assert code == 1, out.getvalue()
    assert "FAILED" in out.getvalue() and "boom-from-the-child" in out.getvalue(), out.getvalue()
    assert "exit 5" in out.getvalue(), out.getvalue()
    assert time.monotonic() - began < 25, "the watch did not end when the child ended"


def test_b6_7_ps_failing_right_after_the_start_reaps_the_child(
    tmp_path: Path, d: Path, procs: list
) -> None:
    # B6-7 — 띄운 직후 시작 시각을 못 읽으면 신원이 없다. 산 자식을 거두고 종료 1.
    made: list[subprocess.Popen] = []
    real_popen = subprocess.Popen

    def popen(*args: object, **kw: object) -> subprocess.Popen:
        proc = real_popen(*args, **kw)  # type: ignore[call-overload]
        made.append(proc)
        procs.append(proc)
        return proc

    out = io.StringIO()
    with pytest.MonkeyPatch.context() as m:
        m.setattr(lp.subprocess, "Popen", popen)
        m.setattr(lp, "PS_COMMAND", _no_ps(tmp_path), raising=False)
        result = lp.start(d, "worker", [*SLEEPER, "--config", f"{d}/worker.toml"],
                          places=_places(d.parents[2]), out=out)  # fmt: skip
    assert result == 1, out.getvalue()
    assert made and made[0].poll() is not None, "the unrecorded child is still running"
    assert "FAILED" in out.getvalue()


def _read_or_fail(call):
    try:
        return call()
    except (OSError, UnicodeDecodeError) as error:
        pytest.fail(f"raised {error!r} instead of BLOCKED")


def test_b6_8_an_unreadable_record_is_blocked_and_stop_all_goes_on(d: Path, procs: list) -> None:
    # B6-8 — 디렉터리인 pid 「파일」 하나에 stop-all 이 멈추지 않는다:
    # 그 이름은 BLOCKED, 나머지는 거둔다.
    (d / "bad.pid").mkdir()
    server = _start(d, "server", [*SLEEPER, "--config", f"{d}/server.toml"], procs)
    out = io.StringIO()
    code = _read_or_fail(lambda: lp.stop_all(d, grace=1.0, out=out))
    lines = out.getvalue().splitlines()
    assert code == 3, lines
    assert any(line.startswith("bad:") and "BLOCKED" in line for line in lines), lines
    assert _rc(server, 5) == -signal.SIGTERM, lines


@pytest.mark.parametrize("which", ["start-not-utf8", "pid-not-utf8", "pid-unreadable"])
def test_b6_8_a_record_that_cannot_be_read_blocks_signal(d: Path, procs: list, which: str) -> None:
    # B6-8 — UTF-8 아님 · 읽을 수 없음도 traceback 이 아니라 BLOCKED.
    if which == "pid-unreadable" and os.geteuid() == 0:
        pytest.skip("root reads a mode-000 file")
    proc = _start(d, "worker", [*SLEEPER, "--config", f"{d}/worker.toml"], procs)
    target = d / ("worker.start" if which.startswith("start") else "worker.pid")
    if which.endswith("not-utf8"):
        target.write_bytes(b"\xff\xfe\x00bad")
    else:
        target.chmod(0)
    try:
        code, lines = _read_or_fail(lambda: _send(d, "worker", "TERM"))
    finally:
        target.chmod(0o644)
    _assert_blocked(code, lines)
    assert proc.poll() is None


def _start_with_blocked(d: Path, procs: list, sig: int, **kw) -> subprocess.Popen:
    # 막힌 신호 마스크는 exec 를 지나 자식에게 간다(#267). 띄우는 동안만 막고 바로 되돌린다.
    old = signal.pthread_sigmask(signal.SIG_BLOCK, {sig})
    try:
        proc = _start(d, "client", [sys.executable, "-c", f"import time; {READY}"], procs, **kw)
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, old)
    _wait_ready(d, "client")
    return proc


def test_b6_9_interactive_sigint_also_clears_a_blocked_mask(d: Path, procs: list) -> None:
    # B6-9 — SIGINT 가 막힌 부모에서 띄워도 `--interactive-sigint` 의 자식은 SIGINT 로 끝난다.
    proc = _start_with_blocked(d, procs, signal.SIGINT, interactive_sigint=True)
    code, lines = _send(d, "client", "INT")
    assert code == 0, lines
    assert _rc(proc, 5) == -signal.SIGINT


def test_b6_9_a_blocked_sigterm_is_not_inherited(d: Path, procs: list) -> None:
    # B6-9 — 플래그 없이도 마스크는 비운다: 막힌 SIGTERM 을 물려받으면
    # stop-all 의 SIGTERM 이 안 닿는다.
    proc = _start_with_blocked(d, procs, signal.SIGTERM)
    code, lines = _send(d, "client", "TERM")
    assert code == 0, lines
    assert _rc(proc, 5) == -signal.SIGTERM


@pytest.mark.parametrize("failing", ["worker.pid", "worker.start"])
def test_b6_10_a_failed_record_write_reaps_the_child(d: Path, procs: list, failing: str) -> None:
    # B6-10 — pid · start 를 못 적으면 그 자식은 신원 없이 산다. 거두고 종료 1,
    # 반쪽 기록은 남기지 않는다.
    real_write = lp._write_atomically
    pids: list[int] = []

    def write(path: Path, text: str) -> None:
        if path.name == "worker.pid":
            pids.append(int(text))
        if path.name == failing:
            raise OSError(28, "No space left on device")
        real_write(path, text)

    out = io.StringIO()
    with pytest.MonkeyPatch.context() as m:
        m.setattr(lp, "_write_atomically", write)
        result = _read_or_fail(
            lambda: lp.start(
                d,
                "worker",
                [*SLEEPER, "--config", f"{d}/worker.toml"],
                places=_places(d.parents[2]),
                out=out,
            )  # fmt: skip
        )
    if isinstance(result, subprocess.Popen):
        procs.append(result)
    assert result == 1, out.getvalue()
    assert pids and not _ps(pids[0], "lstart").strip(), "the unrecorded child is still running"
    assert not (d / "worker.pid").exists() and not (d / "worker.start").exists()


# --- batch-7: 남은 빈틈 ---------------------------------------------------------
# 사례마다 `# B7-<n>` 은 batch-7 in 파일의 항목 번호다. rcm 이 설정 값을 읽는 규칙
# (`config._coerce_scalar`)과 덮어쓰기 순서(플래그 > 환경 > 설정 > 기본값)를 그대로 따른다.


def _server_toml(d: Path, name: str, body: str) -> str:
    (d / name).write_text(f'[server]\n{body}data_dir = "{d}"\n', encoding="utf-8")
    return f"{d}/{name}"


def _worker_toml(d: Path, name: str, server_line: str) -> str:
    (d / name).write_text(f'{server_line}data_dir = "{d}/worker"\n', encoding="utf-8")
    return f"{d}/{name}"


@pytest.mark.parametrize("port", ['"8787"', '" 8787"', '"8787 "', '"08787"', '"\\t8787\\n"'])
def test_b7_1_a_quoted_port_8787_is_refused(tmp_path: Path, d: Path, port: str) -> None:
    # B7-1 — rcm 은 글로 적힌 포트를 `int(value.strip())` 로 읽는다(config.py `_coerce_scalar`).
    # `port = "8787"` 은 정수 8787 과 같은 운영 포트다.
    config = _server_toml(d, "quoted.toml", f"port = {port}\n")
    reason = _refusal(tmp_path / "home", d, ["./.venv/bin/rcm", "serve", "--config", config])
    assert reason and "8787" in reason, reason


def test_b7_1_a_quoted_test_port_is_allowed(tmp_path: Path, d: Path) -> None:
    # B7-1 — 대조: 글로 적힌 시험 포트는 그대로 띄운다.
    config = _server_toml(d, "quoted.toml", 'port = " 8791 "\n')
    assert _refusal(tmp_path / "home", d, ["./.venv/bin/rcm", "serve", "--config", config]) is None


@pytest.mark.parametrize("port", ["true", "8787.0", '"x"', '"8787x"', "[8787]"])
def test_b7_1_a_port_rcm_cannot_read_is_refused(tmp_path: Path, d: Path, port: str) -> None:
    # B7-1 — rcm 이 설정 오류로 거부하는 꼴(참 · 실수 · 숫자 아님)은 무엇으로 뜰지 모른다
    # — 띄우지 않는다.
    config = _server_toml(d, "odd.toml", f"port = {port}\n")
    reason = _refusal(tmp_path / "home", d, ["./.venv/bin/rcm", "serve", "--config", config])
    assert reason and "port" in reason, reason


@pytest.mark.parametrize(
    "server_line",
    ["", 'server = ""\n', 'server = "auto"\n', 'server = " AUTO "\n', 'server = "auto/"\n'],
)
def test_b7_2_a_worker_without_a_server_url_is_refused(
    tmp_path: Path, d: Path, server_line: str
) -> None:
    # B7-2 — 워커 설정에 주소가 없거나 비었거나 `auto` 고 `--server` 도 없으면 `rcm worker` 는 mDNS
    # 로 서버를 찾는다(cli.py `_worker_config` 의 `wants_discovery`) — 운영 서버를 찾을 수 있다.
    config = _worker_toml(d, "w.toml", server_line)
    reason = _refusal(tmp_path / "home", d, ["./.venv/bin/rcm", "worker", "--config", config])
    assert reason and "discover" in reason, reason


@pytest.mark.parametrize(
    "flag", [["--server", "auto"], ["--server", ""], ["--server="], ["--serv", "Auto"]]
)
def test_b7_2_a_worker_told_to_discover_on_the_command_line_is_refused(
    tmp_path: Path, d: Path, flag: list[str]
) -> None:
    # B7-2 — 플래그가 설정을 이긴다: 설정의 시험 주소 위에 `--server auto` 면 찾으러 간다.
    argv = ["./.venv/bin/rcm", "worker", "--config", f"{d}/worker.toml", *flag]
    reason = _refusal(tmp_path / "home", d, argv)
    assert reason and "discover" in reason, reason


@pytest.mark.parametrize(
    ("extra", "environ"),
    [
        ([], {"RCM_SERVER": "auto"}),
        ([], {"RCM_SERVER": " AUTO/"}),
        (["--server", "auto"], {"RCM_SERVER": "http://127.0.0.1:8791"}),
        (["--server", ""], {}),
    ],
)
def test_b7_2_a_client_told_to_discover_is_refused(
    tmp_path: Path, d: Path, extra: list[str], environ: dict[str, str]
) -> None:
    # B7-2 — 클라이언트도 같다: 이긴 주소(`--server` > `RCM_SERVER`)가 `auto` 거나 없으면 찾는다.
    argv = ["./.venv/bin/rcm", "run", "slow", "--by", "acceptance", *extra]
    reason = _refusal(tmp_path / "home", d, argv, environ=environ)
    assert reason and ("discover" in reason), reason


def test_b7_2_a_client_with_a_test_flag_over_auto_is_allowed(tmp_path: Path, d: Path) -> None:
    # B7-2 — 대조: `RCM_SERVER=auto` 여도 `--server` 의 시험 주소가 이긴다.
    argv = ["./.venv/bin/rcm", "run", "slow", "--server", "http://127.0.0.1:8791"]
    assert _refusal(tmp_path / "home", d, argv, environ={"RCM_SERVER": "auto"}) is None


@pytest.mark.parametrize(
    "server_line",
    ["", 'server = "auto"\n', 'server = "http://127.0.0.1:8787"\n'],
)
def test_b7_3_a_worker_flag_server_wins_over_the_config(
    tmp_path: Path, d: Path, server_line: str
) -> None:
    # B7-3 — `rcm worker` 는 `--server` 를 설정 위에 얹는다(config.py `load_worker_config` 의
    # overrides). 설정에 주소가 없어도 · `auto` 여도 · 8787 이어도 이긴 값은 시험 주소다.
    config = _worker_toml(d, "w.toml", server_line)
    argv = ["./.venv/bin/rcm", "worker", "--config", config, "--server", "http://127.0.0.1:8791"]
    assert _refusal(tmp_path / "home", d, argv) is None


@pytest.mark.parametrize(
    ("toml", "extra", "environ"),
    [
        ("port-less", ["--port", "8791"], {}),
        ("port-less", [], {"RCM_SERVER_PORT": "8791"}),
        ("table-less", ["--port", "8791"], {}),
        ("table-less", [], {"RCM_SERVER_PORT": " 8791"}),
        ("port-8787", ["--port=8791"], {}),
        ("port-8787", [], {"RCM_SERVER_PORT": "8791"}),
    ],
)
def test_b7_3_the_server_port_override_wins_over_the_config(
    tmp_path: Path, d: Path, toml: str, extra: list[str], environ: dict[str, str]
) -> None:
    # B7-3 — `rcm serve` 의 포트는 `--port` > `RCM_SERVER_PORT` > 설정 > 8787(config.py
    # `load_server_config`). 이긴 값이 8791 이면 설정에 port 가 없어도 받는다.
    bodies = {
        "port-less": f'[server]\ndata_dir = "{d}"\n',
        "table-less": 'title = "no server table"\n',
        "port-8787": f'[server]\nport = 8787\ndata_dir = "{d}"\n',
    }
    (d / "o.toml").write_text(bodies[toml], encoding="utf-8")
    argv = ["./.venv/bin/rcm", "serve", "--config", f"{d}/o.toml", *extra]
    assert _refusal(tmp_path / "home", d, argv, environ=environ) is None


def test_b7_3_an_override_that_names_8787_still_wins(tmp_path: Path, d: Path) -> None:
    # B7-3 — 대조: 이긴 값이 8787 이면 설정이 8790 이어도 거부한다.
    config = _server_toml(d, "s.toml", "port = 8790\n")
    argv = ["./.venv/bin/rcm", "serve", "--config", config]
    reason = _refusal(tmp_path / "home", d, argv, environ={"RCM_SERVER_PORT": " 8787 "})
    assert reason and "8787" in reason, reason


@pytest.mark.parametrize("which", ["ps-fails", "pid-is-a-directory"])
def test_b7_4_an_unconfirmed_earlier_record_is_not_written_over(
    tmp_path: Path, d: Path, procs: list, which: str
) -> None:
    # B7-4 — 앞의 기록을 확인하지 못하면(ps 실패 · 못 읽는 pid) 그 위에 새로 띄우지 않는다.
    # 덮으면 아직 살아 있을지 모르는 프로세스의 신원을 잃는다.
    if which == "ps-fails":
        earlier = _start(d, "worker", [*SLEEPER, "--config", f"{d}/worker.toml"], procs)
        before = (d / "worker.pid").read_text(encoding="utf-8")
    else:
        earlier = None
        (d / "worker.pid").mkdir()
        before = None
    out = io.StringIO()
    with pytest.MonkeyPatch.context() as m:
        if which == "ps-fails":
            m.setattr(lp, "PS_COMMAND", _no_ps(tmp_path), raising=False)
        result = _read_or_fail(
            lambda: lp.start(d, "worker", [*SLEEPER, "--config", f"{d}/worker.toml"],
                             places=_places(d.parents[2]), out=out)
        )  # fmt: skip
    assert result == 4, out.getvalue()
    assert "cannot be confirmed" in out.getvalue(), out.getvalue()
    if before is not None:
        assert (d / "worker.pid").read_text(encoding="utf-8") == before
        assert earlier is not None and earlier.poll() is None
    else:
        assert (d / "worker.pid").is_dir()


@pytest.mark.parametrize(
    ("script", "said"),
    [("exit 2\n", "exit 2"), ("echo 'ps: bad option' >&2\nexit 1\n", "bad option")],
)
def test_b7_4_an_odd_ps_exit_is_unknown_not_gone(
    tmp_path: Path, d: Path, procs: list, script: str, said: str
) -> None:
    # B7-4 — `ps` 가 0 · 1 이 아닌 종료로 끝나거나 1 에 stderr 를 남기면 그것은
    # 「없는 pid」가 아니다. 아무것도 찍지 않았어도 「not running」으로 읽지 않는다
    # — signal 도 stop-all 도 BLOCKED.
    proc = _start(d, "worker", [*SLEEPER, "--config", f"{d}/worker.toml"], procs)
    odd = tmp_path / "odd-ps"
    odd.write_text(f"#!/bin/sh\n{script}", encoding="utf-8")
    odd.chmod(0o755)
    out = io.StringIO()
    with pytest.MonkeyPatch.context() as m:
        m.setattr(lp, "PS_COMMAND", str(odd), raising=False)
        code, lines = _send(d, "worker", "TERM", expect_config=f"{d}/worker.toml")
        all_code = lp.stop_all(d, grace=0.2, out=out)
    _assert_blocked(code, lines)
    reasons = [line for line in lines if line.startswith("reason:")]
    assert reasons and said in reasons[0] and "not running" not in reasons[0], lines
    assert all_code == 3, out.getvalue()
    assert "not running" not in out.getvalue(), out.getvalue()
    assert proc.poll() is None


def test_b7_4_rcm_server_bind_on_8787_is_refused(tmp_path: Path, d: Path) -> None:
    # B7-4 — `RCM_SERVER_BIND` 도 `--bind` 와 같이 본다(설정의 bind 를 이긴다).
    environ = {"RCM_SERVER_BIND": "127.0.0.1:8787"}
    reason = _refusal(tmp_path / "home", d, _good_server(d), environ=environ)
    assert reason and "8787" in reason and "bind" in reason, reason


@pytest.mark.parametrize(
    ("argv", "environ"),
    [
        ([sys.executable, "-c", "pass"], {"RCM_SERVER_PORT": "8787"}),
        (["./.venv/bin/rcm", "serve", "--config", "{d}/server.toml", "--port", "8791"],
         {"RCM_SERVER_PORT": "8787"}),
        ([sys.executable, "-c", "pass", "--port", "8787"], {}),
        (["./.venv/bin/rcm", "serve", "--config", "{d}/server.toml", "--port", "8787",
          "--port", "8791"], {}),
    ],
)  # fmt: skip
def test_b7_4_an_explicit_8787_is_refused_even_when_it_does_not_win(
    tmp_path: Path, d: Path, argv: list[str], environ: dict[str, str]
) -> None:
    # B7-4 — 사람이 이 명령에 적은 8787(`RCM_SERVER_PORT` · `--port`)은 이긴 값이 아니어도 거부한다.
    # 환경은 이 프로세스가 띄우는 rcm 에게도 물려지고(잡 안의 `rcm serve`), 감싼 스크립트의
    # `--port` 는 안쪽 rcm 에게 넘어간다. 설정이 없는 명령 · 뒤의 플래그가 이기는 명령이라
    # 설정 검사로는 못 잡는다 — 그 규칙만 지키는 사례.
    words = [word.format(d=d) for word in argv]
    reason = _refusal(tmp_path / "home", d, words, environ=environ)
    assert reason and "8787" in reason, reason


def test_b7_6_the_test_folder_passes_the_tools_own_tmp_rule(d: Path) -> None:
    # B7-6 — rcm-ci 잡 4380: 그 잡은 TMPDIR 을 /private/tmp 아래에 두어 pytest 의 tmp_path 도
    # 거기 있다. 도구는 /tmp 아래의 --dir 를 거부하므로(시스템이 도중에 지울 수 있다) 시험 폴더가
    # tmp_path 아래면 진짜 CLI 를 부르는 시험이 그 머신에서만 REFUSED 로 빨개진다. 시험 폴더는
    # 도구의 기본 /tmp 규칙(`Places.tmp_roots` 기본값)을 통과해야 한다.
    places = lp.Places(home=d.parents[2], prod=gp.Production())
    assert lp.refusal(d, SLEEPER, places, cwd=d, environ={}) is None, d


# --- batch-8: 덮어쓰기 완화는 그 값을 읽는 rcm 명령에만 ---------------------------------
# 사례마다 `# B8-<n>` 은 batch-8 in 파일의 항목 번호다.


@pytest.mark.parametrize(
    ("argv", "body", "environ"),
    [
        ([sys.executable, "{d}/wrap.py", "--config", "{c}", "--port", "8791"], "port-less", {}),
        ([sys.executable, "{d}/wrap.py", "--config", "{c}"], "port-less",
         {"RCM_SERVER_PORT": "8791"}),
        (["{d}/wrap.sh", "--config={c}", "--port=8791"], "port-8787", {}),
        (["{d}/wrap.sh", "--config", "{c}"], "table-less", {"RCM_SERVER_PORT": "8791"}),
        (["{d}/wrap.sh", "--config", "{c}", "--server", "http://127.0.0.1:8791"], "url-8787", {}),
        (["{d}/wrap.sh", "--config", "{c}", "--server", "http://127.0.0.1:8791"], "url-auto", {}),
        (["./.venv/bin/rcm", "token", "--config", "{c}"], "port-less",
         {"RCM_SERVER_PORT": "8791"}),
        (["./.venv/bin/rcm", "serve", "--config", "{c}", "--server", "http://127.0.0.1:8791"],
         "url-8787", {}),
    ],
)  # fmt: skip
def test_b8_1_an_override_counts_only_for_the_rcm_command_that_reads_it(
    tmp_path: Path, d: Path, argv: list[str], body: str, environ: dict[str, str]
) -> None:
    # B8-1 — 명령줄 · 환경이 설정의 포트 · 서버를 이긴다고 보는 것은 안쪽 명령이 그 값을 실제로
    # 읽을 때뿐이다: `--port` · `RCM_SERVER_PORT` 는 `rcm serve`, `--server` 는 `rcm worker` 와
    # 클라이언트. 감싼 스크립트(`python wrap.py`)는 그 플래그를 안쪽 rcm 에 넘긴다는 보장이 없고,
    # 넘기지 않으면 rcm 이 설정대로 8787 로 뜬다. 그래서 설정을 적힌 그대로(batch-6 의 엄격한
    # 읽기) 본다. `rcm token` 은 포트를 쓰지 않고, `rcm serve` 는 `--server` 를 받지 않는다.
    bodies = {
        "port-less": f'[server]\ndata_dir = "{d}"\n',
        "port-8787": f'[server]\nport = 8787\ndata_dir = "{d}"\n',
        "table-less": 'title = "no server table"\n',
        "url-8787": f'server = "http://127.0.0.1:8787"\ndata_dir = "{d}/worker"\n',
        "url-auto": f'server = "auto"\ndata_dir = "{d}/worker"\n',
    }
    config = d / "b8.toml"
    config.write_text(bodies[body], encoding="utf-8")
    words = [word.format(d=d, c=config) for word in argv]
    reason = _refusal(tmp_path / "home", d, words, environ=environ)
    assert reason and ("8787" in reason or "discover" in reason), reason


@pytest.mark.parametrize(
    ("argv", "environ"),
    [
        (["./.venv/bin/rcm", "serve", "--config", "{c}", "--port", "8791"], {}),
        (["./.venv/bin/rcm", "serve", "--config", "{c}"], {"RCM_SERVER_PORT": "8791"}),
        ([sys.executable, "{d}/wrap.py", "--config", "{d}/server.toml", "--port", "8791"], {}),
    ],
)  # fmt: skip
def test_b8_1_the_rcm_server_still_takes_its_own_override(
    tmp_path: Path, d: Path, argv: list[str], environ: dict[str, str]
) -> None:
    # B8-1 — 대조: `rcm serve` 는 여전히 port 없는 설정 위의 `--port 8791` 을 받는다. 감싼 명령도
    # 설정 자체가 시험 포트면 받는다.
    config = d / "b8.toml"
    config.write_text(f'[server]\ndata_dir = "{d}"\n', encoding="utf-8")
    words = [word.format(d=d, c=config) for word in argv]
    assert _refusal(tmp_path / "home", d, words, environ=environ) is None


@pytest.mark.parametrize("port", ["true", "false", "8790.0", "[8790]", "{ value = 8790 }"])
def test_b8_2_a_port_of_the_wrong_type_is_refused_even_when_it_is_not_8787(
    tmp_path: Path, d: Path, port: str
) -> None:
    # B8-2 — `_port_value` 의 타입 검사는 rcm 이 설정 오류로 거부하는 꼴을 막는다. 8787 이 아닌
    # 값이라 뒤의 `== 8787` 이 대신 막아 주지 않는다. 이 검사만 잡는 사례다.
    config = _server_toml(d, "typed.toml", f"port = {port}\n")
    reason = _refusal(tmp_path / "home", d, ["./.venv/bin/rcm", "serve", "--config", config])
    assert reason and "cannot read the port" in reason, reason


@pytest.mark.parametrize("server", ["8790", "true", "8790.0", "[8790]"])
def test_b8_2_a_server_key_that_is_neither_table_nor_url_is_refused(
    tmp_path: Path, d: Path, server: str
) -> None:
    # B8-2 — 맨 위의 `server` 가 표도 주소도 아니면(`server = 8790`) rcm 이 설정 오류로 거부한다.
    # 무엇으로 뜰지 모르니 띄우지 않는다. 값이 8787 이 아니어서 포트 검사가 가려 주지 않는다.
    (d / "odd.toml").write_text(f'server = {server}\ndata_dir = "{d}"\n', encoding="utf-8")
    argv = ["./.venv/bin/rcm", "serve", "--config", f"{d}/odd.toml"]
    reason = _refusal(tmp_path / "home", d, argv)
    assert reason and "neither a table nor a URL" in reason, reason


@pytest.mark.parametrize(
    ("extra", "environ"),
    [
        (["--port", "abc"], {}),
        (["--port="], {}),
        ([], {"RCM_SERVER_PORT": ""}),
        ([], {"RCM_SERVER_PORT": "eighty"}),
    ],
)
def test_b8_3_an_override_rcm_cannot_read_is_refused(
    tmp_path: Path, d: Path, extra: list[str], environ: dict[str, str]
) -> None:
    # B8-3 — rcm 은 `--port abc` 를 argparse 에서, `RCM_SERVER_PORT=""` 를 설정 읽기
    # (`_coerce_scalar`)에서 거부한다. 무엇으로 뜰지 모르는 값은 설정이 시험 포트여도 받지 않는다.
    argv = [*_good_server(d), *extra]
    reason = _refusal(tmp_path / "home", d, argv, environ=environ)
    assert reason and "cannot read the port" in reason, reason


def test_b8_4_a_test_folder_that_cannot_be_removed_fails_the_test() -> None:
    # B8-4 — `outside_tmp` 는 진짜 홈에 폴더를 만든다. 못 지우면 조용히 남기지 않고 시험이
    # 실패한다(`ignore_errors` 없음). 그 픽스처는 `tests/conftest.py` 한 곳에만 있다 — 두 시험
    # 파일이 복사본을 따로 들고 있으면 한쪽만 고쳐진다.
    import conftest

    for module in ("test_live_proc.py", "test_skill_links.py"):
        text = (ROOT / "tests" / module).read_text(encoding="utf-8")
        assert "def outside_tmp(" not in text, f"tests/{module} keeps its own outside_tmp"
    make = getattr(conftest, "outside_tmp_folder", None)
    assert make is not None, "tests/conftest.py has no shared outside_tmp_folder"
    if os.geteuid() == 0:  # pragma: no cover — root 는 읽기 전용 폴더도 지운다
        pytest.skip("root removes a read-only folder")
    made: list[Path] = []
    try:
        with pytest.raises(OSError), make() as folder:
            made.append(folder)
            locked = folder / "locked"
            locked.mkdir()
            (locked / "file").write_text("x", encoding="utf-8")
            locked.chmod(0o500)
    finally:
        for folder in made:  # 이 시험이 만든 폴더만 치운다
            (folder / "locked").chmod(0o700)
            shutil.rmtree(folder)
    assert made and not made[0].exists()
