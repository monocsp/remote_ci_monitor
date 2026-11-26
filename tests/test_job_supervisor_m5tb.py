"""러너의 Popen 직전 확인 · 단조 시계 · 그룹 거두기(S7)와 감독자 모듈(M5t-b §4.3 · §4.13).

결정 331 · 333 · 339-c 를 잠근다. 감독자는 실제 파일을 `python -I` 로 띄우고, 부모가 죽는 경우는
가짜 부모 프로세스를 `kill -9` 한다. 시작 확인과 Popen 창의 훅은 감독자 `main()` 의 키워드 인자라서
그 파일을 경로로 읽는 작은 래퍼를 쓴다(명령 줄로는 닿지 않는다).
"""

from __future__ import annotations

import ast
import dataclasses
import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from remote_ci_monitor import job_supervisor, runner
from remote_ci_monitor.core.model import Source
from remote_ci_monitor.runner import RunnerError, RunSpec, run_job

SUP = str(runner.SUPERVISOR_PATH)
MARK = job_supervisor.NOT_STARTED_MARKER


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:  # 좀비는 죽은 것으로 본다
        out = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False
        ).stdout.strip()
    except OSError:
        return True
    return bool(out) and not out.startswith("Z")


def wait_dead(pid: int, seconds: float) -> float | None:
    """죽을 때까지 기다린 초. 그 안에 안 죽으면 None."""
    began = time.monotonic()
    while time.monotonic() - began < seconds:
        if not alive(pid):
            return time.monotonic() - began
        time.sleep(0.02)
    return None


def read_pid(path: Path, seconds: float = 5.0) -> int:
    for _ in range(int(seconds / 0.02)):
        if path.exists() and path.read_text().strip():
            return int(path.read_text().strip())
        time.sleep(0.02)
    raise AssertionError(f"no pid in {path}")


def kill_quietly(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def py_cmd(tmp_path: Path, name: str, body: str) -> list[str]:
    p = tmp_path / f"{name}.py"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return [sys.executable, str(p)]


#: 자식 하나를 남기는 명령 — 자식은 SIGTERM 을 무시하고 pid 를 적으며 표준 출력을 닫는다(파이프를
#: 쥐지 않는다). 명령 자신은 SIGTERM 에 곧 끝난다. `{pidfile}` 은 시험이 채운다
LEAVES_A_TERM_IGNORING_CHILD = """
    import os, signal, sys, time
    pid = os.fork()
    if pid == 0:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        fd = os.open(os.devnull, os.O_WRONLY)
        os.dup2(fd, 1); os.dup2(fd, 2)
        open({pidfile!r}, "w").write(str(os.getpid()))
        while True:
            time.sleep(0.1)
    signal.signal(signal.SIGTERM, lambda *a: os._exit(0))
    while True:
        time.sleep(0.1)
"""


# ══ 감독자 모듈 ══════════════════════════════════════════════════════════════


def test_rl52_the_supervisor_file_imports_only_the_standard_library():
    """`-I` 로 파일 경로를 띄우므로 패키지 import 가 있으면 소스 트리 rcm 에서 못 뜬다(F83)."""
    tree = ast.parse(Path(SUP).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    assert names and names <= set(sys.stdlib_module_names) | {"__future__"}


def test_rl52_the_runner_and_the_supervisor_agree_on_the_marker_and_exit():
    assert runner.SUPERVISOR_NOT_STARTED_EXIT == job_supervisor.SUPERVISOR_NOT_STARTED_EXIT == 125
    assert runner.SUPERVISOR_MARKER == MARK.encode()
    assert job_supervisor.SUPERVISOR_POLL_SECONDS == 0.25


def run_sup(args: list[str], cwd: Path, **kw: Any) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-I", SUP, *args],
        cwd=cwd,
        capture_output=True,
        timeout=20,
        start_new_session=True,  # 러너처럼 새 그룹의 리더로 — 감독자의 killpg 가 시험에 닿지 않게
        **kw,
    )


def test_rl52_a_wrong_parent_starts_nothing_and_exits_125_with_the_marker(tmp_path):
    flag = tmp_path / "ran"
    out = run_sup(["--parent", "1", "--grace", "1", "--", "touch", str(flag)], tmp_path)
    assert out.returncode == 125
    assert out.stdout.startswith(f"{MARK}parent_gone\n".encode())
    assert not flag.exists()


@pytest.mark.parametrize(
    "argv, code",
    [
        (["/nonexistent/rcm-no-such-tool"], "launch_executable_missing"),
        (None, "launch_permission_denied"),
    ],
)
def test_rl52_a_command_that_cannot_start_says_so_with_the_runners_code(tmp_path, argv, code):
    if argv is None:
        f = tmp_path / "not-executable"
        f.write_text("#!/bin/sh\n", encoding="utf-8")
        f.chmod(0o644)
        argv = [str(f)]
    out = run_sup(["--parent", str(os.getpid()), "--grace", "1", "--", *argv], tmp_path)
    assert out.returncode == 125
    assert out.stdout == f"{MARK}{code}\n".encode()


def test_rl52_a_command_that_exits_125_itself_keeps_its_125(tmp_path):
    out = run_sup(
        ["--parent", str(os.getpid()), "--grace", "1", "--", "sh", "-c", "echo hi; exit 125"],
        tmp_path,
    )
    assert out.returncode == 125 and out.stdout == b"hi\n"


def test_rl43_the_command_exit_code_and_signal_pass_through(tmp_path):
    me = str(os.getpid())
    assert (
        run_sup(["--parent", me, "--grace", "1", "--", "sh", "-c", "exit 7"], tmp_path).returncode
        == 7
    )
    killed = run_sup(["--parent", me, "--grace", "1", "--", "sh", "-c", "kill -9 $$"], tmp_path)
    assert killed.returncode == -9


def test_rl52_bad_arguments_are_a_launch_failure_not_a_crash(tmp_path):
    out = run_sup(["--parent", "x", "--", "true"], tmp_path)
    assert out.returncode == 125 and out.stdout == f"{MARK}launch_failed\n".encode()


def hooked(tmp_path: Path, hook: str, parent: str, cmd: list[str]) -> subprocess.CompletedProcess:
    """감독자 파일을 경로로 읽어 훅을 건 `main()` 을 부른다 — 훅은 명령 줄로는 닿지 않는다."""
    wrapper = tmp_path / "hooked.py"
    wrapper.write_text(
        textwrap.dedent(
            f"""
            import importlib.util, os, signal, sys
            spec = importlib.util.spec_from_file_location("sup", {SUP!r})
            sup = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(sup)
            term = lambda: os.kill(os.getpid(), signal.SIGTERM)
            sys.exit(sup.main(sys.argv[1:], {hook}=term))
            """
        ),
        encoding="utf-8",
    )
    return subprocess.run(
        [sys.executable, "-I", str(wrapper), "--parent", parent, "--grace", "5", "--", *cmd],
        cwd=tmp_path,
        capture_output=True,
        timeout=20,
        start_new_session=True,
    )


def test_rl52_a_sigterm_before_the_check_starts_nothing(tmp_path):
    flag = tmp_path / "ran"
    out = hooked(tmp_path, "before_check", str(os.getpid()), ["touch", str(flag)])
    assert out.returncode == 125
    assert out.stdout == f"{MARK}terminated\n".encode()
    assert not flag.exists()


def test_rl51_a_sigterm_in_the_popen_window_reaches_the_command(tmp_path):
    """(a) 시작 확인 뒤 · Popen 전에 온 SIGTERM — 감독자가 Popen 뒤 그룹에 한 번 보낸다."""
    done = tmp_path / "done"
    out = hooked(tmp_path, "before_popen", str(os.getpid()), ["sh", "-c", f"sleep 2; touch {done}"])
    # 시계는 재지 않는다 — 명령이 끝까지 돌았다면 `done` 이 있고 종료 코드가 0 이다
    assert not done.exists()  # 명령은 끝까지 돌지 못했다
    assert out.returncode == -15


def spawn_parent(tmp_path: Path, cmd: list[str], grace: int) -> subprocess.Popen:
    """감독자의 부모 노릇을 하는 프로세스 — 시험이 `kill -9` 해서 워커의 죽음을 흉내 낸다."""
    script = tmp_path / "parent.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import os, subprocess, sys, time
            subprocess.Popen(
                [sys.executable, "-I", {SUP!r}, "--parent", str(os.getpid()),
                 "--grace", "{grace}", "--", *{cmd!r}],
                start_new_session=True,
            )
            time.sleep(60)
            """
        ),
        encoding="utf-8",
    )
    return subprocess.Popen([sys.executable, str(script)], cwd=tmp_path)


@pytest.mark.parametrize("ignores_term", [False, True])
def test_rl43_the_worker_dying_ends_the_command_group(tmp_path, ignores_term):
    """(a) 명령이 SIGTERM 을 따르면 0.25 + ε 안에, (b) 무시하면 0.31 + grace 안에 그룹이
    사라진다."""
    pidfile = tmp_path / "cmd.pid"
    trap = "trap '' TERM; " if ignores_term else ""
    cmd = ["sh", "-c", f"{trap}echo $$ > {pidfile}; while :; do sleep 0.1; done"]
    parent = spawn_parent(tmp_path, cmd, grace=1)
    try:
        pid = read_pid(pidfile)
        parent.kill()
        parent.wait()
        took = wait_dead(pid, 6)
        assert took is not None, "the command outlived its dead worker"
        # 명세의 상한은 (a) 0.25 + ε · (b) 0.31 + grace(1)다. `-n auto` 부하에서도 흔들리지 않게
        # 여유를 두되, 그룹을 끝내지 못하는 감독자(6초 안에 못 죽임)와는 갈린다
        assert took < (4.0 if ignores_term else 3.0)
    finally:
        parent.kill()
        if pidfile.exists():
            kill_quietly(int(pidfile.read_text()))


def test_rl51_the_supervisor_sweeps_a_term_ignoring_child_after_the_command_ends(tmp_path):
    """(b) 워커가 죽은 채: 명령은 SIGTERM 에 곧 끝나지만 SIGTERM 을 무시하는 자식이 남는다.
    감독자가 첫 SIGTERM + grace + 0.5 안에 그룹을 SIGKILL 한다."""
    child = tmp_path / "child.pid"
    cmd = py_cmd(tmp_path, "leaver", LEAVES_A_TERM_IGNORING_CHILD.format(pidfile=str(child)))
    parent = spawn_parent(tmp_path, cmd, grace=1)
    pid = read_pid(child)
    try:
        parent.kill()
        parent.wait()
        took = wait_dead(pid, 6)
        assert took is not None, "the child outlived the sweep"
        # 명세의 상한 0.31 + grace(1) + 0.5 에 부하 여유 2초 — 쓸기가 없으면 6초 안에 죽지 않는다
        assert took < 0.31 + 1 + 0.5 + 2.0
    finally:
        kill_quietly(pid)


# ══ 러너 ════════════════════════════════════════════════════════════════════


class Obs:
    """러너 관찰자 대역. `stop_at` · `cancel_at` 이 서면 그 길로 멈춘다."""

    def __init__(self) -> None:
        self.stop = threading.Event()
        self.cancel = threading.Event()
        self.out = bytearray()
        self.phases: list[str] = []
        self.pids: list[int] = []
        self.on_spawning: Any = None

    def phase(self, phase: str) -> None:
        self.phases.append(phase)

    def output(self, data: bytes) -> None:
        self.out.extend(data)

    def should_cancel(self) -> bool:
        return self.cancel.is_set()

    def should_stop(self) -> bool:
        return self.stop.is_set()

    def spawning(self) -> None:
        if self.on_spawning is not None:
            self.on_spawning()

    def spawned(self, pid: int) -> None:
        self.pids.append(pid)


def spec(tmp_path: Path, argv: list[str], **kw: Any) -> RunSpec:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return RunSpec(
        job_id=1,
        preset_name="ship",
        argv=tuple(argv),
        env={},
        env_passthrough=("PATH",),
        timeout_seconds=None,
        inputs={},
        requester_label="t",
        source=Source(mode="tree", tree_hash="x"),
        workspace=ws,
        log_path=tmp_path / "log.txt",
        **kw,
    )


@pytest.mark.parametrize("irreversible", [False, True])
@pytest.mark.parametrize("flag", ["stop", "cancel"])
def test_rl41_a_flag_raised_while_preparing_starts_no_process_even_without_requires(
    tmp_path, irreversible, flag
):
    """(a) requires 가 없어도 Popen 직전에 본다(결정 333, F65). 표지 파일 0개, `group_gone` 참."""
    ran = tmp_path / "ran"
    obs = Obs()
    s = spec(tmp_path, ["touch", str(ran)], irreversible=irreversible)
    result = run_job(s, obs, materialize=lambda _s: getattr(obs, flag).set())
    assert (result.lost, result.cancelled) == ((True, False) if flag == "stop" else (False, True))
    assert result.rc is None and result.group_gone is True
    assert obs.pids == [] and not ran.exists()


@pytest.mark.parametrize("flag", ["stop", "cancel"])
def test_rl41_c_a_preset_with_requires_and_a_flag_while_preparing_starts_no_process(tmp_path, flag):
    """(c) `requires` 가 있는 표식 프리셋에서 (a): 도구 검사를 지나도 Popen 직전 확인이 끊는다 —
    명령 표지 파일 0개, 감독자 · 명령 pid 0, 결과는 플래그대로, `group_gone` 참."""
    ran = tmp_path / "ran"
    obs = Obs()
    s = spec(tmp_path, ["touch", str(ran)], irreversible=True, requires=("sh", "touch"))
    result = run_job(s, obs, materialize=lambda _s: getattr(obs, flag).set())
    assert (result.lost, result.cancelled) == ((True, False) if flag == "stop" else (False, True))
    assert result.rc is None and result.group_gone is True
    assert obs.pids == [] and not ran.exists()
    assert "executing" not in obs.phases


def test_rl41_a_flag_between_the_check_and_popen_is_caught_by_the_first_immediate_check(
    tmp_path, monkeypatch
):
    """(b) 확인과 Popen 사이에 문턱 — 펌프의 첫 점검이 Popen 직후 곧바로 끊는다(1초 기다리지
    않음)."""
    import remote_ci_monitor.runner as runner_mod

    # 폴링 간격을 20초로 늘려 기준선을 만든다. 첫 점검이 곧바로가 아니면 멈춤은 그 간격만큼
    # 늦는다(≥ 20초). 곧바로면 SIGTERM + grace 1 안에 끝난다. 명세가 허용하는 grace_J + 4초 와도
    # 맞는다(그보다 늦으면 폴링을 기다린 것이다)
    monkeypatch.setattr(runner_mod, "POLL_SECONDS", 20.0)
    obs = Obs()
    obs.on_spawning = obs.stop.set
    began = time.monotonic()
    result = run_job(spec(tmp_path, ["sleep", "30"], grace_seconds=1), obs)
    assert result.lost and obs.pids
    assert time.monotonic() - began < 1 + 4  # grace_J + 4초, 기준선 20초의 4분의 1


def test_rl34_f_sigkill_follows_the_monotonic_clock_when_the_wall_clock_goes_back(tmp_path):
    """(f) SIGTERM 뒤 워커 벽시계가 1시간 뒤로 가도 SIGKILL 은 단조 시계로 grace + 2초 안(F67)."""
    obs = Obs()
    after: list[bool] = []

    def now() -> datetime:
        # 취소 뒤 첫 읽기가 SIGTERM 시각이다 — 그다음 읽기부터 1시간 뒤로 간다
        if obs.cancel.is_set():
            after.append(True)
        return datetime.now(UTC) - (timedelta(hours=1) if len(after) >= 2 else timedelta())

    pidfile = tmp_path / "pid"
    argv = ["sh", "-c", f"trap '' TERM; echo $$ > {pidfile}; sleep 6"]
    t = threading.Timer(0.3, obs.cancel.set)
    t.start()
    began = time.monotonic()
    result = run_job(spec(tmp_path, argv, grace_seconds=1), obs, now_fn=now)
    assert result.cancelled and result.rc == -9
    assert time.monotonic() - began < 0.3 + 1 + 2 + 1


def test_rl43_the_supervisor_wraps_only_an_irreversible_job(tmp_path):
    obs = Obs()
    run_job(spec(tmp_path, ["sh", "-c", "echo $PPID"], irreversible=True), obs)
    leader = obs.pids[0]
    assert int(obs.out.decode().strip()) == leader  # 명령의 부모가 감독자다
    plain = Obs()
    run_job(spec(tmp_path, ["sh", "-c", "echo $$"]), plain)
    assert int(plain.out.decode().strip()) == plain.pids[0]


def test_rl43_exit_codes_through_the_supervisor_are_the_commands(tmp_path):
    seven = run_job(spec(tmp_path, ["sh", "-c", "exit 7"], irreversible=True), Obs())
    assert seven.rc == 7 and seven.group_gone is True
    nine = run_job(spec(tmp_path, ["sh", "-c", "kill -9 $$"], irreversible=True), Obs())
    assert nine.rc == -9
    own = run_job(spec(tmp_path, ["sh", "-c", "exit 125"], irreversible=True), Obs())
    assert own.rc == 125  # 표지가 없다 — 명령의 125 그대로(RL52 c)


def test_rl52_a_missing_command_under_the_supervisor_has_the_same_code(tmp_path):
    with pytest.raises(RunnerError) as plain:
        run_job(spec(tmp_path, ["/nonexistent/rcm-no-such-tool"]), Obs())
    with pytest.raises(RunnerError) as wrapped:
        run_job(spec(tmp_path, ["/nonexistent/rcm-no-such-tool"], irreversible=True), Obs())
    assert plain.value.code == wrapped.value.code == "launch_executable_missing"
    assert getattr(wrapped.value, "group_gone", None) is True


def test_rl52_b_a_non_executable_command_under_the_supervisor_has_the_same_code(tmp_path):
    """RL52 (b) 실행 권한이 없다 — 러너 수준에서도 감독자 없을 때와 같은 `launch_permission_denied`
    이고, 감독자의 실패가 명령의 종료 1 이나 `launch_failed` 로 보이지 않는다."""
    f = tmp_path / "not-executable"
    f.write_text("#!/bin/sh\necho ran\n", encoding="utf-8")
    f.chmod(0o644)
    with pytest.raises(RunnerError) as plain:
        run_job(spec(tmp_path, [str(f)]), Obs())
    with pytest.raises(RunnerError) as wrapped:
        run_job(spec(tmp_path, [str(f)], irreversible=True), Obs())
    assert plain.value.code == wrapped.value.code == "launch_permission_denied"
    assert getattr(wrapped.value, "group_gone", None) is True


def test_rl52_a_missing_supervisor_file_is_a_broken_install(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "SUPERVISOR_PATH", tmp_path / "gone.py")
    with pytest.raises(RunnerError) as e:
        run_job(spec(tmp_path, ["true"], irreversible=True), Obs())
    assert e.value.code == "launch_failed" and e.value.args_public == {"error": "SupervisorMissing"}


def test_rl52_fake_modules_on_pythonpath_and_in_the_workspace_are_not_read(tmp_path):
    """(d) 잡 env 의 PYTHONPATH 와 워크스페이스의 가짜 `signal.py` — 감독자는 읽지 않고(`-I`) 명령은
    받은 env 그대로다."""
    fake = tmp_path / "fake"
    fake.mkdir()
    sentinel = tmp_path / "fake-signal-was-imported"
    body = f"open({str(sentinel)!r}, 'w').write('x')\nraise SystemExit(99)\n"
    (fake / "signal.py").write_text(body, encoding="utf-8")
    s = spec(tmp_path, ["sh", "-c", 'printf %s "$PYTHONPATH"'], irreversible=True)
    (s.workspace / "signal.py").write_text(body, encoding="utf-8")
    s = dataclasses.replace(s, env={"PYTHONPATH": str(fake)})
    obs = Obs()
    result = run_job(s, obs)
    assert result.rc == 0 and not sentinel.exists()
    assert obs.out.decode() == str(fake) + "\n"


def test_rl43_d_a_killed_supervisor_is_reaped_by_the_runner(tmp_path):
    """(d) 감독자만 `kill -9`(워커는 산 채) — 러너가 리더 끝을 보고 S7 로 남은 그룹을 거둔다. 명령
    pid 가 리더 끝 + grace + 2초 안에 사라지고 결과는 −9(리더의 것), `group_gone` 참."""
    child = tmp_path / "child.pid"
    cmd = py_cmd(tmp_path, "leaver", LEAVES_A_TERM_IGNORING_CHILD.format(pidfile=str(child)))
    obs = Obs()
    box: dict[str, Any] = {}

    def kill_leader() -> None:
        pid = read_pid(child)
        box["child"] = pid
        os.kill(obs.pids[0], signal.SIGKILL)
        box["leader_end"] = time.monotonic()

    threading.Thread(target=kill_leader, daemon=True).start()
    try:
        result = run_job(spec(tmp_path, cmd, irreversible=True, grace_seconds=1), obs)
        assert result.rc == -9 and result.group_gone is True
        assert not alive(box["child"])
        assert time.monotonic() - box["leader_end"] < 1 + 2 + 0.5
    finally:
        if "child" in box:
            kill_quietly(box["child"])


@pytest.mark.slow
def test_rl51_c_the_runner_reaps_from_its_first_sigterm_not_from_the_leader_end(tmp_path):
    """(c) 러너가 취소로 SIGTERM 을 보낸 뒤 grace 를 거의 다 쓰고 리더가 끝났다(감독자 `kill -9`).
    S7 의 SIGKILL 시한은 첫 SIGTERM + grace 다 — 리더 끝 + grace 면 2·grace 가 나온다."""
    child = tmp_path / "child.pid"
    cmd = py_cmd(tmp_path, "leaver", LEAVES_A_TERM_IGNORING_CHILD.format(pidfile=str(child)))
    obs = Obs()
    box: dict[str, Any] = {}
    grace = 3

    def cancel_then_kill_leader() -> None:
        box["child"] = read_pid(child)
        obs.cancel.set()
        box["term"] = time.monotonic()
        time.sleep(grace * 0.8)
        os.kill(obs.pids[0], signal.SIGKILL)

    threading.Thread(target=cancel_then_kill_leader, daemon=True).start()
    try:
        result = run_job(spec(tmp_path, cmd, irreversible=True, grace_seconds=grace), obs)
        assert result.cancelled and result.group_gone is True
        assert wait_dead(box["child"], 10) is not None
        assert time.monotonic() - box["term"] < grace + 1.5
    finally:
        if "child" in box:
            kill_quietly(box["child"])


def test_rl51_b_a_live_worker_cancel_sweeps_a_term_ignoring_child_from_the_first_sigterm(
    tmp_path,
):
    """(b) 워커가 산 채: 러너가 취소로 SIGTERM 을 보내고 명령은 곧 끝나지만 SIGTERM 을 무시하는
    자식이 남는다. 남은 자식은 첫 SIGTERM + `grace_J` + 0.5초 안에 사라지고(감독자의 쓸기와 러너
    S7 가운데 먼저 닿는 쪽), 결과는 `cancelled` · `group_gone` 참이다."""
    child = tmp_path / "child.pid"
    cmd = py_cmd(tmp_path, "leaver", LEAVES_A_TERM_IGNORING_CHILD.format(pidfile=str(child)))
    obs = Obs()
    box: dict[str, Any] = {}
    grace = 1

    def cancel() -> None:
        box["child"] = read_pid(child)
        box["term"] = time.monotonic()
        obs.cancel.set()
        took = wait_dead(box["child"], 10)
        box["dead_at"] = None if took is None else time.monotonic()

    t = threading.Thread(target=cancel, daemon=True)
    t.start()
    try:
        result = run_job(spec(tmp_path, cmd, irreversible=True, grace_seconds=grace), obs)
        t.join(12)
        assert result.cancelled and result.group_gone is True
        assert box["dead_at"] is not None, "the term-ignoring child outlived the cancel"
        # 명세의 상한 grace + 0.5 에 러너 폴링(0.25) · 부하 여유 1.5초 — 쓸기가 없으면 끝나지 않는다
        assert box["dead_at"] - box["term"] < grace + 0.5 + 1.5
    finally:
        if "child" in box:
            kill_quietly(box["child"])


@pytest.mark.parametrize("flag", ["stop", "cancel"])
def test_rl52_a_a_supervisor_that_sees_the_wrong_parent_logs_the_marker_and_the_flag_wins(
    tmp_path, monkeypatch, flag
):
    """(a) 러너를 거쳐: 감독자가 기대한 부모를 못 보면 명령은 0회, 잡 로그 첫 줄이
    `[rcm] supervisor: not started: parent_gone` 이고 결과는 러너 플래그(stop → lost,
    cancel → cancelled)를 따른다 — 감독자 실패로 바뀌지 않는다."""
    real = runner.supervised_argv

    def wrong_parent(s: RunSpec) -> list[str]:
        argv = real(s)
        argv[argv.index("--parent") + 1] = "1"
        return argv

    monkeypatch.setattr(runner, "supervised_argv", wrong_parent)
    ran = tmp_path / "ran"
    obs = Obs()
    s = spec(tmp_path, ["touch", str(ran)], irreversible=True)

    def output(data: bytes) -> None:
        obs.out.extend(data)
        getattr(obs, flag).set()  # 감독자가 표지를 낸 뒤 · 러너의 끝 점검 전에 선 플래그

    obs.output = output  # type: ignore[method-assign]
    result = run_job(s, obs)
    assert not ran.exists()  # 명령 0회
    first = bytes(obs.out).split(b"\n", 1)[0]  # 잡 로그로 가는 스트림
    assert first == MARK.encode() + b"parent_gone"
    assert (result.lost, result.cancelled) == ((True, False) if flag == "stop" else (False, True))


def test_rl52_a_without_a_flag_the_wrong_parent_is_a_launch_failure(tmp_path, monkeypatch):
    real = runner.supervised_argv

    def wrong_parent(s: RunSpec) -> list[str]:
        argv = real(s)
        argv[argv.index("--parent") + 1] = "1"
        return argv

    monkeypatch.setattr(runner, "supervised_argv", wrong_parent)
    ran = tmp_path / "ran"
    with pytest.raises(RunnerError) as e:
        run_job(spec(tmp_path, ["touch", str(ran)], irreversible=True), Obs())
    assert e.value.code == "launch_failed"
    assert e.value.args_public == {"error": "SupervisorNotStarted"}
    assert not ran.exists()


def test_rl52_e_a_worker_from_a_source_tree_starts_the_supervisor_and_the_command(tmp_path):
    """(e) rcm 을 설치하지 않은 파이썬에서 소스 트리를 `PYTHONPATH` 로 읽은 러너 — 감독자(`-I` 로
    파일 경로)가 뜨고 명령이 돈다(F83)."""
    base = getattr(sys, "_base_executable", sys.executable)
    src = Path(runner.__file__).resolve().parents[1]
    probe = subprocess.run([base, "-I", "-c", "import remote_ci_monitor"], capture_output=True)
    if probe.returncode == 0:
        pytest.skip("this python has rcm installed — no source-tree-only interpreter here")
    ran = tmp_path / "ran"
    script = tmp_path / "from_src.py"
    script.write_text(
        textwrap.dedent(
            f"""
            from pathlib import Path
            import remote_ci_monitor
            from remote_ci_monitor.core.model import Source
            from remote_ci_monitor.runner import RunSpec, run_job

            assert remote_ci_monitor.__file__.startswith({str(src)!r}), remote_ci_monitor.__file__

            class Obs:
                def phase(self, p): pass
                def output(self, d): pass
                def should_cancel(self): return False
                def should_stop(self): return False
                def spawning(self): pass
                def spawned(self, pid): pass

            ws = Path({str(tmp_path / "ws")!r})
            ws.mkdir()
            spec = RunSpec(
                job_id=1, preset_name="ship", argv=("touch", {str(ran)!r}), env={{}},
                env_passthrough=("PATH",), timeout_seconds=None, inputs={{}},
                requester_label="t", source=Source(mode="tree", tree_hash="x"), workspace=ws,
                log_path=Path({str(tmp_path / "log.txt")!r}), irreversible=True,
            )
            r = run_job(spec, Obs())
            print("rc", r.rc, r.group_gone)
            """
        ),
        encoding="utf-8",
    )
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(src)}
    out = subprocess.run(
        [base, str(script)], capture_output=True, text=True, env=env, timeout=30, cwd=tmp_path
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "rc 0 True"
    assert ran.exists()


def test_rl49_a_descendant_that_left_the_session_is_not_seen(tmp_path):
    """명령이 `setsid` 로 세션을 빠져나간 자손을 남기고 끝난다. rcm 은 그 자손을 끊지 않는다(지금
    할 수 있는 일이 아님을 고정한다). 그룹은 사라져 `group_gone` 은 참이다(§1.3 · §9)."""
    pidfile = tmp_path / "daemon.pid"
    cmd = py_cmd(
        tmp_path,
        "daemonize",
        f"""
        import os, time
        if os.fork() == 0:
            os.setsid()
            fd = os.open(os.devnull, os.O_WRONLY)
            os.dup2(fd, 1); os.dup2(fd, 2)
            open({str(pidfile)!r}, "w").write(str(os.getpid()))
            time.sleep(30)
            os._exit(0)
        while not os.path.exists({str(pidfile)!r}):
            time.sleep(0.02)
        """,
    )
    try:
        result = run_job(spec(tmp_path, cmd, irreversible=True, grace_seconds=1), Obs())
        assert result.rc == 0 and result.group_gone is True
        assert alive(read_pid(pidfile))
    finally:
        if pidfile.exists():
            kill_quietly(int(pidfile.read_text()))
