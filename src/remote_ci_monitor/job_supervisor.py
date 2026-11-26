"""되돌릴 수 없는 명령의 감독자(M5t-b §4.13 · 결정 331 · 339-c).

러너가 `irreversible` 프리셋의 명령을 이 파일로 감싸 띄운다::

    [sys.executable, "-I", <이 파일의 절대 경로>, "--parent", <러너 pid>, "--grace", <grace_J>,
     "--", *argv]

`-I` 라서 잡 env 의 `PYTHONPATH` 와 워크스페이스(cwd)의 가짜 모듈을 읽지 않는다(F83). 그래서 이
파일은 **표준 라이브러리만** import 한다 — 패키지 import 가 없어야 소스 트리에서 띄운 rcm 에서도
뜬다(시험이 import 목록을 잠근다).

감독자는 새 세션 · 새 그룹의 리더이고(러너의 `start_new_session=True`), 명령은 같은 그룹의 자식이다.
러너의 `killpg` 는 오늘처럼 명령에 닿는다. 감독자가 더하는 것은 셋이다.

1. 시작 확인: 부모가 `--parent` 가 아니거나 이미 SIGTERM 을 받았으면 명령을 띄우지 않고 표지 줄과
   종료 125 로 끝난다.
2. 부모 감시: `getppid()` 가 바뀌면(워커가 죽었다) 그룹에 SIGTERM, 첫 SIGTERM + grace 에 SIGKILL.
3. 명령 끝 뒤 쓸기: SIGTERM 을 받았거나 부모가 바뀌었으면 남은 그룹 멤버를 첫 SIGTERM + grace 까지
   기다렸다가 그룹에 SIGKILL 한다.
"""

from __future__ import annotations

import os
import sys

#: 명령을 띄우지 못했다 — POSIX `env` · `nohup` 이 도구 자신의 실패에 쓰는 값(결정 339-c (3))
SUPERVISOR_NOT_STARTED_EXIT = 125
#: 부모 감시 · 쓸기의 박자(실측 F78)
SUPERVISOR_POLL_SECONDS = 0.25
#: 표준 출력 첫 바이트부터 나오는 미시작 표지. 러너가 종료 125 와 함께 읽는다
NOT_STARTED_MARKER = "[rcm] supervisor: not started: "


def _not_started(reason: str) -> int:
    try:
        os.write(1, f"{NOT_STARTED_MARKER}{reason}\n".encode())
    except OSError:
        pass
    return SUPERVISOR_NOT_STARTED_EXIT  # mutcheck: supervisor-failure-exit-one


def _parse(argv: list[str]) -> tuple[int, float, list[str]]:
    if "--" not in argv:
        raise ValueError("missing --")
    cut = argv.index("--")
    head, cmd = argv[:cut], argv[cut + 1 :]
    opts = dict(zip(head[0::2], head[1::2], strict=True))
    if set(opts) != {"--parent", "--grace"} or not cmd:
        raise ValueError("bad arguments")
    return int(opts["--parent"]), float(opts["--grace"]), cmd


def _launch_code(e: OSError) -> str:
    """러너 `_launch_error` 와 같은 분류(`runner.py`) — 감독자가 있어도 요약 코드가 같다."""
    import errno

    if e.errno == errno.ENOENT:
        return "launch_executable_missing"
    if e.errno in (errno.EACCES, errno.EPERM):
        return "launch_permission_denied"
    return "launch_failed"


def _killpg(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _members_left(pgid: int) -> bool:
    """자기 그룹에 자기와 이 `ps` 말고 남은 멤버가 있나(F84). `ps` 를 못 돌리면 「남았다」."""
    import subprocess

    env = dict(os.environ, LC_ALL="C")
    try:
        ps = subprocess.Popen(
            ["ps", "-A", "-o", "pid=", "-o", "pgid="],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            env=env,
        )
        out, _ = ps.communicate(timeout=5)
    except (OSError, subprocess.SubprocessError):
        return True
    if ps.returncode != 0:
        return True
    me = os.getpid()
    for line in out.decode("ascii", "replace").splitlines():
        parts = line.split()
        if len(parts) != 2 or not all(p.isdigit() for p in parts):
            continue
        pid, group = int(parts[0]), int(parts[1])
        if group == pgid and pid not in (me, ps.pid):
            return True
    return False


def _exit_like(status: int) -> None:
    """명령과 같은 종료 상태로 끝난다. 신호로 죽었으면 같은 신호로 끝나 러너가 오늘과 같은 `-s` 를
    본다."""
    import signal

    if os.WIFSIGNALED(status):
        sig = os.WTERMSIG(status)
        try:
            signal.signal(sig, signal.SIG_DFL)
        except (OSError, ValueError):  # SIGKILL · SIGSTOP 은 처리기를 바꿀 수 없다
            pass
        os.kill(os.getpid(), sig)
        os._exit(128 + sig)
    os._exit(os.WEXITSTATUS(status) if os.WIFEXITED(status) else 1)


def main(
    argv: list[str] | None = None,
    *,
    before_check: object = None,
    before_popen: object = None,
) -> int:
    """`before_check` · `before_popen` 은 시험의 훅이다(호출 가능한 것). 명령 줄로는 닿지 않는다."""
    args = sys.argv[1:] if argv is None else argv
    # Popen 이 돌아오기 전의 실패는 모두 표지 + 125 다 — 명령이 뜨지 않았다(결정 339-c (3))
    try:
        import signal
        import subprocess
        import threading
        import time

        parent, grace, cmd = _parse(args)
        term: list[float] = []

        def on_term(signum: int, frame: object) -> None:
            if not term:
                term.append(time.monotonic())

        # 처리기는 exec 에서 기본으로 돌아가므로 명령에 상속되지 않는다(SIG_IGN 은 상속된다)
        signal.signal(signal.SIGTERM, on_term)
        if callable(before_check):
            before_check()
        if os.getppid() != parent:  # mutcheck: supervisor-no-parent-check
            return _not_started("parent_gone")
        if term:
            return _not_started("terminated")
        if callable(before_popen):
            before_popen()
        try:
            child = subprocess.Popen(cmd)
        except OSError as e:
            return _not_started(_launch_code(e))
        popen_done = time.monotonic()
    except Exception:  # noqa: BLE001 — 감독자 자신의 시작 실패도 명령 전이면 같은 표지
        return _not_started("launch_failed")

    pgid = os.getpgrp()
    # 시작 확인 뒤 · Popen 이 돌아오기 전에 온 SIGTERM 은 명령이 못 받았을 수 있다. 한 번 다시
    # 보낸다(결정 339-c (1))
    if term and term[0] < popen_done:  # mutcheck: supervisor-popen-window-term-dropped
        _killpg(pgid, signal.SIGTERM)

    done = threading.Event()
    status: list[int] = []

    def reap() -> None:
        # 막는 waitpid — 자식이 끝나면 곧바로 깬다(폴링 sleep 이면 잡 끝이 늦는다, F78)
        while True:
            try:
                _, st = os.waitpid(child.pid, 0)
            except InterruptedError:
                continue
            except ChildProcessError:
                st = 0
            status.append(st)
            done.set()
            return

    threading.Thread(target=reap, name="rcm-supervisor-reap", daemon=True).start()
    parent_changed = False

    def watch_parent() -> None:
        nonlocal parent_changed
        if parent_changed or os.getppid() == parent:  # mutcheck: supervisor-ignores-parent-death
            return
        parent_changed = True
        if not term:
            term.append(time.monotonic())
            _killpg(pgid, signal.SIGTERM)

    while not done.wait(SUPERVISOR_POLL_SECONDS):
        watch_parent()
        if term and time.monotonic() >= term[0] + grace:
            _killpg(pgid, signal.SIGKILL)  # 감독자 자신도 함께 끝난다
    watch_parent()
    if not term and not parent_changed:
        _exit_like(status[0])
    # 받았거나 바뀌었다 — 남은 멤버를 첫 SIGTERM + grace 까지 보고, 그때도 남으면 그룹 SIGKILL
    while _members_left(pgid):  # mutcheck: supervisor-no-post-command-sweep
        if time.monotonic() >= term[0] + grace:
            _killpg(pgid, signal.SIGKILL)
        time.sleep(SUPERVISOR_POLL_SECONDS)
    _exit_like(status[0])
    return 0  # 닿지 않는다


if __name__ == "__main__":
    sys.exit(main())
