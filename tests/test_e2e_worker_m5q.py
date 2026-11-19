"""원격 워커의 진짜 execve (M5q PR B1 · B2 · `docs/m5q-test-scenarios-b.md` 의 e2e).

`make_bootloader` 가 pip 없는 venv 에 지금 빌드의 wheel 을 풀어 비 editable 설치본을 만든다. 그
설치본의 `rcm worker` 를 하위 프로세스로 띄우고, pytest 안의 진짜 서버(`WorkerServer`)가 버전을
올린 척한다(그 버전의 가짜 wheel 을 싣는다). 워커가 기동 ① 과 기동 등록 409 ② 에서 같은
인터프리터를 `-P -c BOOT` 로 다시 실행하는지, PID 가 그대로인지, 서버의 워커 행이 서버 버전으로
등록되는지를 본다. 환경은 처음부터 만든다(`child_env`) — 사용자의 `~/.cache` 를 보지 않는다.

B2 는 도는 워커가 heartbeat 에서 서버 버전이 바뀐 것을 보고 드레인한 뒤 설치본 부트로더로 같은
PID 에서 다시 실행하는지 본다. 감독자 흉내(`Supervisor`)는 launchd `SuccessfulExit = false` 와
systemd `Restart=on-failure` 의 규칙으로 0 이 아닌 종료만 다시 띄우고 띄운 횟수를 센다.

시험을 먼저 썼다 — 구현 전에는 빨갛다.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from follow_helpers import (
    INSTALLED,
    NEW,
    NEW2,
    child_env,
    entries,
    fake_wheel,
    make_bootloader,
    sha,
)
from remote_ci_monitor import follow, remote_worker
from remote_ci_monitor.config import parse_preset
from remote_ci_monitor.core.model import QUEUED, SUCCEEDED
from test_cli_worker import WORKER, make_server
from test_server import sh

pytestmark = pytest.mark.skipif(os.name != "posix", reason="follow is POSIX only (결정 172)")

FALLBACK = "rcm: follow: the cached client did not load — running installed"


@pytest.fixture(scope="session")
def bootloader(tmp_path_factory):
    return make_bootloader(tmp_path_factory.mktemp("boot") / "venv")


class Stack:
    """진짜 서버 하나 + 그 서버를 보는 설치본 워커."""

    def __init__(self, tmp: Path, boot):
        self.tmp = tmp
        self.boot = boot
        self.srv = make_server(tmp, worker_claim_wait_seconds=1, worker_heartbeat_seconds=1)
        self.url = f"http://127.0.0.1:{self.srv.port}"
        self.paths: list[str] = []
        self.registers: list[str | None] = []
        self.before_register: list[Any] = []
        self.procs: list[subprocess.Popen] = []
        base = self.srv.httpd.RequestHandlerClass
        outer = self

        def parse_request(handler) -> bool:
            ok = base.parse_request(handler)
            outer.paths.append(handler.path)
            return ok

        self.srv.httpd.RequestHandlerClass = type(
            "Counting", (base,), {"parse_request": parse_request}
        )
        real = self.srv.app.worker_register

        def register(token, body):
            self.registers.append(body.get("version") if isinstance(body, dict) else None)
            if self.before_register:
                self.before_register.pop(0)()
            return real(token, body)

        self.srv.app.worker_register = register  # type: ignore[method-assign]

    def bump(self, version: str) -> None:
        wheel = fake_wheel(version)
        app = self.srv.app
        app.version = version
        app._wheel = (wheel, sha(wheel))
        app._wheel_error = None
        app._wheel_done = True

    @property
    def health(self) -> int:
        return sum(1 for p in self.paths if p == "/api/health")

    @property
    def client(self) -> int:
        return sum(1 for p in self.paths if p.startswith("/client/"))

    @property
    def clients(self) -> Path:
        return Path(os.path.realpath(self.tmp / "cache")) / "rcm" / "clients"

    def env(self) -> dict[str, str]:
        return child_env(
            self.tmp,
            self.boot,
            RCM_WORKER_TOKEN=self.srv.tokens[WORKER],
            PYTHONUNBUFFERED="1",
        )

    def argv(self) -> list[str]:
        return [
            str(self.boot.rcm),
            "worker",
            "--server",
            self.url,
            "--pool",
            "linux",
            "--lanes",
            "1",
            "--data",
            str(self.tmp / "worker-data"),
        ]

    def spawn(self, log: Path) -> subprocess.Popen:
        f = log.open("ab")
        p = subprocess.Popen(
            self.argv(), env=self.env(), stdout=f, stderr=subprocess.STDOUT, cwd=str(self.tmp)
        )
        f.close()
        self.procs.append(p)
        return p

    def run(self, timeout: float = 60) -> subprocess.CompletedProcess:
        return subprocess.run(
            self.argv(),
            env=self.env(),
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(self.tmp),
        )

    def wait_registered(self, version: str, timeout: float = 45) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            row = self.srv.store.get_worker(WORKER)
            if row is not None and row.version == version:
                return
            time.sleep(0.1)
        raise AssertionError(f"worker never registered as v{version}: {self.registers}")

    def close(self) -> None:
        for p in self.procs:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(20)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait(5)
        self.srv.close()


@pytest.fixture
def stack(tmp_path, bootloader):
    s = Stack(tmp_path, bootloader)
    yield s
    s.close()


def command_of(pid: int) -> str:
    ps = shutil.which("ps") or "/bin/ps"
    return subprocess.run(
        [ps, "-o", "command=", "-p", str(pid)], capture_output=True, text=True, timeout=10
    ).stdout


def test_worker_follows_on_start_in_the_same_process(stack, tmp_path):
    # B2 · W1: 진짜 exec 뒤 서버 버전으로 등록한다. 자식은 health 를 다시 부르지 않는다
    stack.bump(NEW)
    log = tmp_path / "worker.log"
    p = stack.spawn(log)
    stack.wait_registered(NEW)
    assert p.poll() is None, log.read_text()
    assert stack.registers == [NEW]
    assert stack.health == 1 and stack.client == 1, stack.paths
    assert " -P -c " in command_of(p.pid)  # 같은 PID 가 BOOT 로 돈다
    text = log.read_text()
    assert f"rcm: following server v{NEW} (installed v{INSTALLED})" in text
    assert f"rcm worker {WORKER}" in text


def test_worker_follows_a_409_in_the_same_process(stack, tmp_path):
    # W2 · §3.2 ②: ① 은 같은 버전을 봤고 등록 직전에 서버가 올라갔다
    stack.before_register.append(lambda: stack.bump(NEW))
    log = tmp_path / "worker.log"
    p = stack.spawn(log)
    stack.wait_registered(NEW)
    assert p.poll() is None, log.read_text()
    assert stack.registers == [INSTALLED, NEW]
    assert stack.health == 2 and stack.client == 1, stack.paths
    assert " -P -c " in command_of(p.pid)
    text = log.read_text()
    assert f"[rcm worker] register: server runs v{NEW} — following" in text
    assert "install the same release" not in text


def test_followed_child_restarts_through_the_installed_rcm_on_a_409(stack, tmp_path):
    # E19 · §14 B질문7(B2): 따라간 자식이 등록 직전에 서버가 또 바뀌면 설치본 부트로더로 바로 다시
    # 실행한다. 설치본이 ① 부터 해서 새 버전이 된다 — 같은 프로세스이고 감독자가 필요 없다
    stack.bump(NEW)
    stack.before_register.append(lambda: stack.bump(NEW2))
    log = tmp_path / "worker.log"
    p = stack.spawn(log)
    stack.wait_registered(NEW2)
    assert p.poll() is None, log.read_text()
    assert stack.registers == [NEW, NEW2]
    assert stack.client == 2, stack.paths
    text = log.read_text()
    assert f"[rcm worker] register: server runs v{NEW2} — following" in text
    assert "install the same release" not in text and "Traceback" not in text


def test_broken_cache_is_not_entered_again(stack, tmp_path):
    # E18 · §14.6 B질문14: 표지는 있고 코드가 깨진 폴더 — 첫 실행은 BOOT 가 `.bad` 를 남기고
    # 설치본으로 돌아가 종료 2, 다음 실행은 그 폴더로 들어가지 않고 이유를 말하며 종료 2.
    # 다시 받지 않고 트레이스백도 없다
    stack.bump(NEW)
    root = Path(os.path.realpath(tmp_path / "cache")) / "rcm"
    health = stack.srv.app.health()[1]
    key = follow.normalize_server(stack.url)
    entry = follow.fetch(key, None, follow.parse_health(key, health, now=time.time()), root=root)
    (entry.path / "remote_ci_monitor" / "cli.py").unlink()
    fetched_before = stack.client
    first = stack.run()
    assert first.returncode == 2, first.stdout + first.stderr
    assert FALLBACK in first.stderr
    assert "· not following: the cached client did not load" in first.stderr
    assert (entry.path / ".bad").exists()
    # B1 격리 검증: 첫 실패(BOOT 가 `.bad` 를 남긴 판)에도 치우는 안내가 한 번 나온다
    remove = f"rcm: remove {entry.path} to fetch it again"
    assert first.stderr.count(remove) == 1, first.stderr
    second = stack.run()
    assert second.returncode == 2, second.stdout + second.stderr
    assert FALLBACK not in second.stderr
    assert f"· not following: the cached client v{NEW} did not load before" in second.stderr
    # 안내는 한 번이고, 따라가지 못할 폴더를 두고 「following」이라고 말하지 않는다
    assert second.stderr.count(remove) == 1, second.stderr
    assert "— following" not in second.stderr
    for r in (first, second):
        assert "Traceback" not in r.stderr
    assert stack.client == fetched_before  # 같은 sha 를 다시 받지 않는다
    assert len(entries(stack.clients)) == 1


# ── PR B2: 드레인 뒤 같은 PID 로 다시 실행 ────────────────────────────────


DRAINING = "draining: no new claims, restarting when running jobs finish"


class Supervisor:
    """launchd · systemd 흉내 — 0 이 아닌 종료면 0.2초 뒤 다시 띄운다. 띄운 프로세스를 남긴다."""

    def __init__(self, stack: Stack, log: Path):
        self.stack = stack
        self.log = log
        self.spawned: list[subprocess.Popen] = []
        self.done = False
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self) -> None:
        while not self.done:
            p = self.stack.spawn(self.log)
            self.spawned.append(p)
            code = p.wait()
            if self.done or code == 0:
                return
            time.sleep(0.2)

    def close(self) -> None:
        self.done = True


def add_presets(stack: Stack, marks: Path) -> None:
    m = str(marks)
    hold = f"touch {m}/started.$RCM_JOB_ID; while [ ! -e {m}/go.$RCM_JOB_ID ]; do sleep 0.05; done"
    stack.srv.cfg.presets = (
        *stack.srv.cfg.presets,
        parse_preset(sh("hold", hold, pool="linux")),
        parse_preset(sh("envdump", "env", pool="linux")),
    )


def wait_for(cond: Any, what: str, log: Path, timeout: float = 45) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {what}:\n{log.read_text()}")


def job_log(stack: Stack, jid: int) -> str:
    status, _headers, body = stack.srv.req("GET", f"/jobs/{jid}/log", token="alice", raw=True)
    assert status == 200, body
    return body.decode("utf-8", errors="replace")


def test_drain_restarts_in_the_same_process_without_losing_the_job(stack, tmp_path):
    # B10 · B14 · W6 · W15 · W16 · G19 · G20 · §3.4: 서버가 올라가면 도는 잡을 끝까지
    # 보고한 뒤 같은 PID 가 설치본 부트로더로 다시 실행해 새 버전으로 등록한다. 감독자가 띄운
    # 횟수는 1 이고 한 로그 파일에 드레인 줄과 두 번째 기동 줄이 이어진다. 드레인 뒤에 넣은 잡은
    # 재실행한 프로세스가 집고, 그 잡의 환경에는 따라가기 변수와 재실행 시각이 없다. 재실행 시각은
    # ① 의 exec 를 넘어 자식에게 가므로 300초 안의 다음 ③ 은 드레인하지 않는다
    marks = tmp_path / "marks"
    marks.mkdir()
    add_presets(stack, marks)
    log = tmp_path / "worker.log"
    sup = Supervisor(stack, log)
    try:
        stack.wait_registered(INSTALLED)
        p = sup.spawned[0]
        assert " -P -c " not in command_of(p.pid)
        jid = stack.srv.queued_job(preset="hold")
        wait_for(lambda: (marks / f"started.{jid}").exists(), "hold started", log)
        stack.bump(NEW)
        wait_for(lambda: DRAINING in log.read_text(), "draining", log)
        nxt = stack.srv.queued_job(preset="envdump")
        time.sleep(1.5)
        assert stack.srv.store.get_job(nxt).state == QUEUED
        (marks / f"go.{jid}").touch()
        stack.wait_registered(NEW)
        wait_for(lambda: stack.srv.store.get_job(nxt).state == SUCCEEDED, "next job", log)
        job = stack.srv.store.get_job(jid)
        assert job.state == SUCCEEDED, (job.state, job.summary)
        assert job.summary != remote_worker.STOP_SUMMARY
        assert stack.registers == [INSTALLED, NEW]
        assert len(sup.spawned) == 1 and p.poll() is None, log.read_text()
        assert " -P -c " in command_of(p.pid)
        text = log.read_text()
        order = [
            f"[rcm worker] server runs v{NEW} — {DRAINING}",
            f"[rcm worker] drained — restarting through the installed rcm for v{NEW}",
            f"rcm: following server v{NEW} (installed v{INSTALLED})",
        ]
        at = [text.index(line) for line in order]
        assert at == sorted(at), text
        assert text.count(f"[rcm worker] rcm worker {WORKER} · pool linux") == 2, text
        env = job_log(stack, nxt)
        assert "RCM_JOB_ID=" in env
        assert "RCM_WORKER_RESTARTED_AT" not in env and "RCM_FOLLOWED" not in env
        stack.bump(NEW2)
        wait_for(
            lambda: f"server runs v{NEW2} — not draining: restarted" in log.read_text(),
            "suppressed inside the window",
            log,
        )
        assert log.read_text().count(DRAINING) == 1
    finally:
        sup.close()


def test_worker_follows_the_server_back_down(stack, tmp_path):
    # B13 · G4 · 결정 150: 따라간 자식은 자기 버전과 견주므로 서버와 같으면 드레인하지 않는다.
    # 서버가 설치본 버전으로 내려가면 드레인하고, 다시 실행한 설치본이 ① 에서 서버와 같음을 보고
    # exec 없이 그 버전으로 등록한다. `/client/` 는 처음 한 번뿐이다
    stack.bump(NEW)
    log = tmp_path / "worker.log"
    p = stack.spawn(log)
    stack.wait_registered(NEW)
    time.sleep(2.5)  # heartbeat 1초 — 같은 버전이면 드레인하지 않는다
    assert DRAINING not in log.read_text()
    stack.bump(INSTALLED)
    stack.wait_registered(INSTALLED)
    assert p.poll() is None, log.read_text()
    assert stack.registers == [NEW, INSTALLED]
    assert stack.client == 1, stack.paths
    text = log.read_text()
    assert f"[rcm worker] server runs v{INSTALLED} — {DRAINING}" in text
    assert "Traceback" not in text
