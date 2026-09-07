"""e2e(M5b-3) — 진짜 `rcm serve` + 진짜 `rcm worker`, 같은 머신의 두 프로세스.

명세는 docs/m5b3-workplan.md §2(`rcm worker` 프로세스) · §4(시나리오 1–7) 와
docs/m5b2-workplan.md §3–§4(서버 규칙: `worker_timeout_seconds` 뒤 lost · 재등록은 옛 잡을 닫는다 ·
취소는 heartbeat 으로). 워커가 등록 전에 죽으면(예: `rcm worker` 가 없어 argparse 종료 2) 픽스처가
종료 코드와 로그째로 실패한다 — 구현 전에는 그 모양으로 빨갛다.

배치:
- 서버: `rcm serve --config server.toml`. 임시 data dir · `lanes = 1` · `grace_seconds = 2` ·
  `worker_timeout_seconds = 10` · `worker_heartbeat_seconds = 1` · `worker_claim_wait_seconds = 2` ·
  `snapshot_cache = true`. 프리셋은 전부 `pool = "linux"` 라 로컬 레인은 아무것도 돌리지 않는다.
- 워커: `rcm worker --server URL --pool linux --lanes 1 --data <dir> [--config worker.toml]`.
  토큰은 `RCM_WORKER_TOKEN`(`rcm token --config server.toml add build-02 --worker`) 으로만 준다.
  워커 이름 = 토큰 이름 `build-02`, 레인 표시 이름 `build-02/1`.
- 클라이언트: `rcm run · cancel · logs · top`(토큰 `laptop`). 폴링은 `/api/status` · `/jobs/{id}` 를
  urllib 로 직접 읽는다(프로세스 스폰 없이 50 ms 간격).
- 오래 도는 잡 스크립트는 자기 pid 를 `RCM_MARK_DIR/<job_id>.pid` 에 적는다 — 취소·정지 뒤 잡
  프로세스가 죽었는지 보고, `kill -9` 로 고아가 된 잡은 teardown 이 killpg 로 치운다.
- 모든 기다림은 마감 있는 폴링이다(pytest-timeout 플러그인 없음). 시나리오 하나 < 40초.
- teardown: 워커·서버를 killpg 로 죽이고, 이 서버 포트를 가리키는 `worker --server …` 프로세스가
  하나도 안 남았음을 `pgrep -f` 로 확인한다(`python -m remote_ci_monitor.cli worker …` 로 띄우므로
  `pgrep -f "rcm worker"` 는 아무것도 못 잡는다 — 포트로 좁힌 패턴을 쓴다).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from gitrepo import HELLO, build_remote
from remote_ci_monitor.core.model import Progress
from remote_ci_monitor.core.progress import progress_from_markers
from remote_ci_monitor.core.status import parse_iso
from remote_ci_monitor.store import Store

ROOT = Path(__file__).resolve().parents[1]
RCM = [sys.executable, "-m", "remote_ci_monitor.cli"]
WORKER = "build-02"  # 워커 토큰 이름 = 워커 이름
LANE = f"{WORKER}/1"  # server.workers[].display_name
CLIENT = "laptop"  # 클라이언트 토큰 이름 — 취소 요약 `cancelled by laptop`
POOL = "linux"
GRACE = 2  # [server] grace_seconds
WORKER_TIMEOUT = 10  # [server] worker_timeout_seconds (하한)
HEARTBEAT = 1  # [server] worker_heartbeat_seconds
CLAIM_WAIT = 2  # [server] worker_claim_wait_seconds
POLL = 0.05
TERMINAL = ("succeeded", "failed", "timed_out", "cancelled", "lost")
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")

# 명세 §4 의 `lin` 마커 스크립트. 스텝 끝 마커의 프로토콜 표기는 `::rcm::step-end::<ok|fail>`
# (core/progress.py) — 명세 본문의 `step_end::1 ok` 는 마커로 안 읽히므로 프로토콜 쪽을 따른다.
# 어느 쪽이든 스텝 2/2 · summary `built ok` 다.
LIN_SCRIPT = (
    "echo ::rcm::steps::2; echo ::rcm::step::build; sleep 0.2; echo ::rcm::step-end::ok; "
    "echo ::rcm::step::test; echo ::rcm::step-end::ok; echo ::rcm::summary::built ok"
)
# 잡 프로세스의 pid(= 프로세스 그룹, 러너가 start_new_session 으로 띄운다)를 적어 둔다
PID_LINE = 'echo $$ > "$RCM_MARK_DIR/$RCM_JOB_ID.pid"'
SLOWL_SCRIPT = f"{PID_LINE}; exec sleep 30"
SLOW4_SCRIPT = f"{PID_LINE}; sleep 4; {LIN_SCRIPT}"
DEPLOY_SCRIPT = "cat hello.txt; echo RCM_REF=$RCM_REF; echo HEAD=$(git rev-parse HEAD)"

SERVER_TOML = """\
[server]
bind = "127.0.0.1"
port = {port}
data_dir = "{data_dir}"
grace_seconds = 2
lanes = 1
snapshot_cache = true
worker_timeout_seconds = 10
worker_heartbeat_seconds = 1
worker_claim_wait_seconds = 2

[[presets]]
name = "lin"
pool = "linux"
argv = ["sh", "-c", '{lin}']
timeout_seconds = 60

[[presets]]
name = "qal"
pool = "linux"
concurrency_group = "devices"
argv = ["sh", "-c", "sleep 3"]
timeout_seconds = 60

[[presets]]
name = "slowl"
pool = "linux"
argv = ["sh", "-c", '{slowl}']
timeout_seconds = 120
[presets.env]
RCM_MARK_DIR = "{marks}"

[[presets]]
name = "slow4"
pool = "linux"
argv = ["sh", "-c", '{slow4}']
timeout_seconds = 60
[presets.env]
RCM_MARK_DIR = "{marks}"

[[presets]]
name = "cachedl"
pool = "linux"
argv = ["sh", "scripts/cached.sh"]
timeout_seconds = 60
{repos}"""

# git_ref 시나리오에서만 붙는다 — 서버는 ls-remote 로 ref 를 확정할 뿐, fetch 는 워커가 한다
REPOS_TOML = """
[[repos]]
name = "app"
url = "{url}"

[[presets]]
name = "deploy"
pool = "linux"
argv = ["sh", "-c", '{script}']
source_modes = ["git_ref"]
repo = "app"
timeout_seconds = 60
"""

# 워커 쪽 worker.toml — 서버와 같은 이름의 [[repos]] 만. 나머지(server · pool · lanes · data)는
# 플래그로 준다
WORKER_TOML = """\
[[repos]]
name = "app"
url = "{url}"
"""

CACHED_SH = """#!/bin/sh
echo "::rcm::step::size"
wc -c < assets/blob.bin
echo "::rcm::summary::cached ok"
"""


# ── 도우미 ───────────────────────────────────────────────────────────────────


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def rcm(*args: str, env: dict[str, str], cwd: Path | None = None, timeout: float = 60):
    return subprocess.run(
        [*RCM, *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout
    )


def last_json(out: str) -> dict[str, Any]:
    lines = [ln for ln in out.strip().splitlines() if ln.startswith("{")]
    assert lines, f"no JSON line in stdout: {out!r}"
    return json.loads(lines[-1])


def clean_env(home: Path) -> dict[str, str]:
    """개발자의 rcm 설정·HOME(git 설정 포함)이 끼어들지 않는 프로세스 환경."""
    drop = ("RCM_SERVER", "RCM_TOKEN", "RCM_CONFIG", "RCM_WORKER_TOKEN", "XDG_CONFIG_HOME")
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env.update(
        PYTHONPATH=str(ROOT / "src"),
        PYTHONUNBUFFERED="1",
        HOME=str(home),
        GIT_CONFIG_NOSYSTEM="1",
        GIT_TERMINAL_PROMPT="0",
    )
    return env


def wait_until(pred: Callable[[], Any], timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        time.sleep(POLL)
    raise AssertionError(f"timed out after {timeout}s waiting for {what}")


def is_dead(pid: int) -> bool:
    """없거나(ESRCH) zombie 면 죽은 것으로 본다."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    out = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, timeout=10
    ).stdout.strip()
    return out == "" or out.startswith("Z")


def kill_group(pid: int) -> None:
    """잡 프로세스 그룹(없으면 프로세스만) 을 SIGKILL. 이미 없으면 조용히."""
    for kill in (os.killpg, os.kill):
        try:
            kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def pgrep_workers(port: int) -> list[str]:
    """이 서버 포트를 가리키는 `… worker --server http://127.0.0.1:<port> …` 프로세스들."""
    out = subprocess.run(
        ["pgrep", "-f", f"worker --server http://127.0.0.1:{port}"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return out.stdout.split()


def remote_lanes(doc: dict[str, Any]) -> list[dict[str, Any]]:
    return [w for w in doc["server"]["workers"] if w["worker"] is not None]


def lane_view(doc: dict[str, Any]) -> list[tuple[str, str, int | None]]:
    return [(w["display_name"], w["state"], w["job_id"]) for w in remote_lanes(doc)]


def pool_of(doc: dict[str, Any], name: str) -> dict[str, Any]:
    pool = next((p for p in doc["pools"] if p["name"] == name), None)
    assert pool is not None, f"pool {name} missing: {[p['name'] for p in doc['pools']]}"
    return pool


# ── 프로세스 ─────────────────────────────────────────────────────────────────


class E2EServer:
    """`rcm serve` 프로세스 하나. 설정·data dir·로그는 tmp 안. 같은 설정으로 다시 띄울 수 있다."""

    def __init__(self, tmp_path: Path, *, marks: Path, repo_url: str | None = None):
        self.port = free_port()
        self.data_dir = tmp_path / "server-data"
        self.config = tmp_path / "server.toml"
        self.log = tmp_path / "server.log"
        home = tmp_path / "home"
        home.mkdir(exist_ok=True)
        self.env = clean_env(home)
        repos = REPOS_TOML.format(url=repo_url, script=DEPLOY_SCRIPT) if repo_url else ""
        self.config.write_text(
            SERVER_TOML.format(
                port=self.port,
                data_dir=self.data_dir,
                marks=marks,
                lin=LIN_SCRIPT,
                slowl=SLOWL_SCRIPT,
                slow4=SLOW4_SCRIPT,
                repos=repos,
            )
        )
        self.proc: subprocess.Popen | None = None
        self._out = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def token(self, name: str, *, admin: bool = False, worker: bool = False) -> str:
        flags = ["--admin"] if admin else (["--worker"] if worker else [])
        out = rcm("token", "--config", str(self.config), "add", name, *flags, env=self.env)
        assert out.returncode == 0, out.stderr
        return out.stdout.strip()

    def start(self, timeout: float = 30) -> None:
        self._out = self.log.open("ab")
        self.proc = subprocess.Popen(
            [*RCM, "serve", "--config", str(self.config)],
            env=self.env,
            stdout=self._out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise AssertionError(f"rcm serve exited {self.proc.returncode}:\n{self.log_text()}")
            try:
                status, _ = self.health()
            except OSError:
                status = 0
            if status == 200:
                return
            time.sleep(0.1)
        raise AssertionError(f"server did not start:\n{self.log_text()}")

    def stop(self, sig: signal.Signals = signal.SIGTERM, timeout: float = 15) -> None:
        """신호를 프로세스에 보내고 종료를 기다린다(늦으면 killpg)."""
        if self.proc is not None and self.proc.poll() is None:
            self.proc.send_signal(sig)
            try:
                self.proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.kill()
        self._close_out()

    def kill(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            kill_group(self.proc.pid)
            self.proc.wait(timeout=10)
        self._close_out()

    def _close_out(self) -> None:
        if self._out is not None:
            self._out.close()
            self._out = None

    def log_text(self) -> str:
        return self.log.read_text(errors="replace") if self.log.exists() else ""

    def get(self, path: str, token: str | None = None) -> tuple[int, Any]:
        """GET → (status, JSON). 연결 실패는 OSError(URLError) 로 올라온다."""
        req = urllib.request.Request(self.url + path)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                raw = resp.read()
                return resp.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw) if raw else {}
            except ValueError:
                return e.code, {"raw": raw.decode(errors="replace")}

    def health(self) -> tuple[int, dict[str, Any]]:
        return self.get("/api/health")

    def client_env(self, token: str) -> dict[str, str]:
        return {**self.env, "RCM_SERVER": self.url, "RCM_TOKEN": token}


class WorkerProc:
    """`rcm worker` 프로세스 하나(pool linux · lanes 1). 토큰은 `RCM_WORKER_TOKEN` 으로만 준다."""

    def __init__(
        self,
        server: E2EServer,
        token: str,
        data_dir: Path,
        log: Path,
        *,
        config: Path | None = None,
    ):
        self.server = server
        self.data_dir = data_dir
        self.log = log
        self.argv = [
            *RCM,
            "worker",
            "--server",
            server.url,
            "--pool",
            POOL,
            "--lanes",
            "1",
            "--data",
            str(data_dir),
        ]
        if config is not None:
            self.argv += ["--config", str(config)]
        self.env = {**server.env, "RCM_WORKER_TOKEN": token}
        self.proc: subprocess.Popen | None = None
        self._out = None

    def start(self, client_token: str, timeout: float = 20) -> None:
        self._out = self.log.open("ab")
        self.proc = subprocess.Popen(
            self.argv,
            env=self.env,
            stdout=self._out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self.wait_registered(client_token, timeout)

    def wait_registered(self, client_token: str, timeout: float) -> None:
        """`server.workers[]` 에 build-02 레인이 idle/busy 로 보일 때까지. 먼저 죽으면 로그째."""
        assert self.proc is not None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise AssertionError(
                    f"rcm worker exited {self.proc.returncode} before registering "
                    f"(argv {self.argv[3:]}):\n{self.log_text()}"
                )
            try:
                _, doc = self.server.get("/api/status", client_token)
            except OSError:
                doc = None
            if doc and any(
                w["worker"] == WORKER and w["state"] in ("idle", "busy") for w in remote_lanes(doc)
            ):
                return
            time.sleep(POLL * 2)
        raise AssertionError(f"worker did not register within {timeout}s:\n{self.log_text()}")

    def signal(self, sig: signal.Signals) -> None:
        assert self.proc is not None
        self.proc.send_signal(sig)

    def wait_exit(self, timeout: float) -> int:
        assert self.proc is not None
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            raise AssertionError(
                f"rcm worker still running {timeout}s after the signal:\n{self.log_text()}"
            ) from None
        finally:
            if self.proc.poll() is not None:
                self._close_out()

    def kill(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            kill_group(self.proc.pid)
            self.proc.wait(timeout=10)
        self._close_out()

    def _close_out(self) -> None:
        if self._out is not None:
            self._out.close()
            self._out = None

    def log_text(self) -> str:
        return self.log.read_text(errors="replace") if self.log.exists() else ""


class Stack:
    """서버 하나 + 워커 n 개 + 클라이언트 환경. 잡 조회는 HTTP 직접."""

    def __init__(self, tmp_path: Path, *, repo_url: str | None = None):
        self.tmp = tmp_path
        self.marks = tmp_path / "marks"
        self.marks.mkdir()
        self.server = E2EServer(tmp_path, marks=self.marks, repo_url=repo_url)
        self.client_token = self.server.token(CLIENT)
        self.worker_token = self.server.token(WORKER, worker=True)
        self.env = self.server.client_env(self.client_token)
        self.worker_data = tmp_path / "worker-data"  # 워커의 data dir — 서버 것과 다르다
        self.workers: list[WorkerProc] = []

    def start(self, *, worker_config: Path | None = None) -> WorkerProc:
        self.server.start()
        return self.spawn_worker(config=worker_config)

    def spawn_worker(self, *, config: Path | None = None) -> WorkerProc:
        n = len(self.workers) + 1
        w = WorkerProc(
            self.server,
            self.worker_token,
            self.worker_data,
            self.tmp / f"worker-{n}.log",
            config=config,
        )
        self.workers.append(w)
        w.start(self.client_token)
        return w

    @property
    def worker(self) -> WorkerProc:
        return self.workers[-1]

    def status(self) -> dict[str, Any]:
        status, doc = self.server.get("/api/status", self.client_token)
        assert status == 200, (status, doc)
        return doc

    def job(self, job_id: int) -> dict[str, Any]:
        status, doc = self.server.get(f"/jobs/{job_id}", self.client_token)
        assert status == 200, (status, doc)
        return doc

    def wait_state(self, job_id: int, states: tuple[str, ...], timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        last: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            try:
                last = self.job(job_id)
            except OSError:
                last = None  # 서버 재시작 중
            if last is not None and last["state"] in states:
                return last
            time.sleep(POLL)
        seen = last["state"] if last else "unreachable"
        raise AssertionError(f"job #{job_id} is {seen}, wanted {states} within {timeout}s")

    def wait_terminal(self, job_id: int, timeout: float) -> dict[str, Any]:
        return self.wait_state(job_id, TERMINAL, timeout)

    def wait_pid(self, job_id: int, timeout: float = 10) -> int:
        """잡 스크립트가 적은 자기 pid — 스크립트가 진짜 돌기 시작했다는 뜻."""
        path = self.marks / f"{job_id}.pid"
        wait_until(lambda: path.exists() and path.read_text().strip(), timeout, f"{path.name}")
        return int(path.read_text().strip())

    def submit(self, tree: Path, preset: str) -> int:
        out = rcm("run", preset, "--no-wait", "--no-join", env=self.env, cwd=tree)
        assert out.returncode == 0, out.stderr
        return int(last_json(out.stdout)["job_id"])

    def progress(self, job_id: int) -> Progress:
        """서버가 워커 로그에서 파싱해 둔 마커 → Progress(종료 뷰에는 progress 가 없어서 DB 로)."""
        view = self.job(job_id)
        store = Store(self.server.data_dir / "rcm.sqlite3")
        try:
            markers = store.markers(job_id)
        finally:
            store.close()
        started = parse_iso(view["started_at"])
        finished = parse_iso(view["finished_at"]) if view.get("finished_at") else None
        return progress_from_markers(
            markers,
            started_at=started,
            finished_at=finished,
            now=finished or started,
            exit_code=view.get("exit_code"),
        )

    def teardown(self) -> None:
        for w in self.workers:
            w.kill()
        self.server.kill()
        for pidfile in self.marks.glob("*.pid"):  # kill -9 로 고아가 된 잡 프로세스
            text = pidfile.read_text().strip()
            if text.isdigit():
                kill_group(int(text))
        leftover = pgrep_workers(self.server.port)
        assert leftover == [], f"rcm worker processes leaked: {leftover}"


class StatusWatcher(threading.Thread):
    """`/api/status` 를 50 ms 마다 읽어 `server.workers[]` 의 (worker, display_name, state, job_id)
    를 모은다 — 잡이 짧아도 「build-02/1 busy #N」 순간을 놓치지 않게."""

    def __init__(self, stack: Stack):
        super().__init__(name="status-watcher", daemon=True)
        self.stack = stack
        self.seen: list[tuple[str | None, str | None, str, int | None]] = []
        self._halt = threading.Event()  # Thread._stop() 을 가리지 않게
        self._lock = threading.Lock()

    def run(self) -> None:
        while not self._halt.is_set():
            try:
                doc = self.stack.status()
            except (OSError, AssertionError):
                doc = None
            if doc is not None:
                for w in doc["server"]["workers"]:
                    item = (w["worker"], w["display_name"], w["state"], w["job_id"])
                    with self._lock:
                        if item not in self.seen:
                            self.seen.append(item)
            self._halt.wait(POLL)

    def __enter__(self) -> StatusWatcher:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._halt.set()
        self.join(timeout=5)

    def busy(self, job_id: int) -> bool:
        with self._lock:
            return (WORKER, LANE, "busy", job_id) in self.seen

    def local_touched(self, job_id: int) -> bool:
        with self._lock:
            return any(w[0] is None and w[3] == job_id for w in self.seen)


# ── 픽스처 ───────────────────────────────────────────────────────────────────


@pytest.fixture
def make_stack(tmp_path: Path):
    made: list[Stack] = []

    def factory(*, repo_url: str | None = None) -> Stack:
        st = Stack(tmp_path, repo_url=repo_url)
        made.append(st)
        return st

    yield factory
    for st in made:
        st.teardown()


@pytest.fixture
def stack(make_stack) -> Stack:
    return make_stack()


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "cached.sh").write_text(CACHED_SH)
    (root / "README.md").write_text("e2e worker\n")
    return root


# ── 1. rcm run lin → 워커가 돌리고 스텝·요약·로그가 서버에 ───────────────────


def test_lin_job_runs_on_the_remote_worker_with_steps_summary_and_the_whole_log(stack, tree):
    stack.start()
    assert lane_view(stack.status()) == [(LANE, "idle", None)]
    with StatusWatcher(stack) as watch:
        out = rcm("run", "lin", "--timeout", "30", env=stack.env, cwd=tree)
    assert out.returncode == 0, out.stderr
    body = last_json(out.stdout)
    jid = body["id"]
    assert body["state"] == "succeeded" and body["summary"] == "built ok", body
    assert body["wait_exit_code"] == 0 and body["exit_code"] == 0 and body["pool"] == POOL
    # 도는 동안 `build-02/1 busy #N` 이었다가 끝나면 idle — 로컬 레인은 이 잡을 만진 적 없다
    assert watch.busy(jid), watch.seen
    assert not watch.local_touched(jid), watch.seen
    assert lane_view(stack.status()) == [(LANE, "idle", None)]
    # 스텝 2/2 — 서버가 워커의 로그(`POST /worker/jobs/{id}/log`)에서 마커를 파싱해 둔 것
    prog = stack.progress(jid)
    assert (prog.steps_total, prog.steps_done, prog.summary) == (2, 2, "built ok")
    assert [s.name for s in prog.steps] == ["build", "test"]
    assert prog.steps_total_partial is False
    # 로그 전체가 서버에 있다(`rcm logs N` == 서버 파일)
    logs = rcm("logs", str(jid), env=stack.env)
    assert logs.returncode == 0, logs.stderr
    assert "::rcm::steps::2\n" in logs.stdout and "::rcm::summary::built ok\n" in logs.stdout
    assert logs.stdout.count("::rcm::step-end::ok\n") == 2
    assert logs.stdout == (stack.server.data_dir / "jobs" / str(jid) / "log.txt").read_text()
    # `rcm top --json` 의 linux 풀 최근 행 · 서버 오류 없음
    top = json.loads(rcm("top", "--json", env=stack.env).stdout)
    row = next(r for r in pool_of(top, POOL)["recent"] if r["id"] == jid)
    assert row["state"] == "succeeded" and row["summary"] == "built ok"
    assert pool_of(top, POOL)["lanes"] == 1 and top["server"]["last_error"] is None
    assert LANE in rcm("top", env=stack.env).stdout  # 텍스트 화면에도 원격 레인 필


# ── 2. 캐시 잡 — 서버가 조립한 tar 를 워커가 받는다 ──────────────────────────


def test_cached_tree_job_runs_on_the_worker_from_the_server_assembled_tar(stack, tree):
    stack.start()
    (tree / "assets").mkdir()
    (tree / "assets" / "blob.bin").write_bytes(os.urandom(1_000_000))  # 압축 안 되는 1 MB
    with StatusWatcher(stack) as watch:
        first = rcm("run", "cachedl", "--timeout", "30", env=stack.env, cwd=tree)
        assert first.returncode == 0, first.stderr
        b1 = last_json(first.stdout)
        assert b1["state"] == "succeeded" and b1["summary"] == "cached ok", b1
        assert b1["source"]["uploaded_bytes"] >= 1_000_000  # 첫 업로드는 다 보낸다
        assert b1["source"]["cached_bytes"] == 0
        second = rcm("run", "cachedl", "--no-join", "--timeout", "30", env=stack.env, cwd=tree)
        assert second.returncode == 0, second.stderr
        b2 = last_json(second.stdout)
    assert b2["id"] != b1["id"] and b2["state"] == "succeeded" and b2["summary"] == "cached ok"
    assert "cache" in second.stderr, second.stderr  # `uploading #N: 0.0 / 1.0 MB (cache 100%)`
    assert b2["source"]["uploaded_bytes"] <= 4096, b2["source"]
    assert b2["source"]["cached_bytes"] >= 1_000_000, b2["source"]
    assert watch.busy(b1["id"]) and watch.busy(b2["id"]), watch.seen
    jobs = stack.server.data_dir / "jobs"
    assert (jobs / str(b2["id"]) / "manifest.json").is_file()  # 클라이언트는 manifest 만 보냈다
    assert (jobs / str(b2["id"]) / "tree.tar.gz").is_file()  # 서버가 워커용으로 조립해 둔 tar
    for jid in (b1["id"], b2["id"]):  # 워크스페이스에 blob 이 있었다 — `wc -c` 가 찍힌다
        assert "1000000\n" in (jobs / str(jid) / "log.txt").read_text(), jid
    top = json.loads(rcm("top", "--json", env=stack.env).stdout)
    assert top["server"]["snapshot_cache"]["blobs"] >= 1
    assert top["server"]["last_error"] is None


# ── 3. 취소 — heartbeat 의 cancel 목록으로 워커가 알아 SIGTERM ───────────────


def test_cancel_reaches_the_worker_through_the_heartbeat_within_seconds(stack, tree):
    stack.start()
    jid = stack.submit(tree, "slowl")
    stack.wait_state(jid, ("running",), 15)
    pid = stack.wait_pid(jid)
    assert not is_dead(pid)
    assert lane_view(stack.status()) == [(LANE, "busy", jid)]
    t0 = time.monotonic()
    cancel = rcm("cancel", str(jid), env=stack.env)
    assert cancel.returncode == 0 and last_json(cancel.stdout)["state"] == "cancelling", cancel
    fin = stack.wait_terminal(jid, timeout=5)
    took = time.monotonic() - t0
    # 워커가 확인한 취소다 — 서버가 대신 닫으면 `worker did not confirm the cancel` 이 된다
    assert fin["state"] == "cancelled" and fin["summary"] == f"cancelled by {CLIENT}", fin
    assert fin["cancelled_by"] == CLIENT and took < 5, (fin, took)
    states = [t["state"] for t in fin["transitions"]]
    assert states[-3:] == ["running", "cancelling", "cancelled"], states
    wait_until(lambda: is_dead(pid), 5, f"job process {pid} to die")
    assert lane_view(stack.status()) == [(LANE, "idle", None)]
    out = rcm("wait", "--job", str(jid), env=stack.env)
    assert out.returncode == 2 and last_json(out.stdout)["state"] == "cancelled"


# ── 4. kill -9 — timeout 뒤 lost · down · lanes 0 · 새 워커가 이어받는다 ──────


def test_killed_worker_leaves_the_job_lost_after_the_timeout_and_a_new_worker_takes_over(
    stack, tree
):
    worker = stack.start()
    jid = stack.submit(tree, "slowl")
    stack.wait_state(jid, ("running",), 15)
    pid = stack.wait_pid(jid)
    t0 = time.monotonic()
    os.kill(worker.proc.pid, signal.SIGKILL)  # 워커만 — 잡(sleep)은 고아로 남는다(진짜 크래시처럼)
    assert worker.wait_exit(5) == -signal.SIGKILL
    fin = stack.wait_terminal(jid, timeout=20)
    took = time.monotonic() - t0
    assert fin["state"] == "lost", fin
    assert re.fullmatch(rf"worker {WORKER} unreachable for \d+s", fin["summary"]), fin["summary"]
    assert WORKER_TIMEOUT - 2 <= took <= 20, took  # timeout 전에는 lost 가 아니다
    doc = stack.status()
    assert [(w["display_name"], w["state"]) for w in remote_lanes(doc)] == [(LANE, "down")]
    assert pool_of(doc, POOL)["lanes"] == 0
    assert stack.server.health()[1]["pools_without_workers"] == [POOL]
    kill_group(pid)  # 고아 잡은 아무도 안 치운다 — 테스트가 치운다
    # 워커를 다시 띄우면 등록되고(옛 잡은 이미 lost 라 안 건드린다) 다음 잡을 받는다
    stack.spawn_worker()
    assert stack.job(jid)["summary"] == fin["summary"]  # `restarted without the job` 로 안 바뀐다
    doc = stack.status()
    assert lane_view(doc) == [(LANE, "idle", None)] and pool_of(doc, POOL)["lanes"] == 1
    assert stack.server.health()[1]["pools_without_workers"] == []
    out = rcm("run", "lin", "--timeout", "30", env=stack.env, cwd=tree)
    assert out.returncode == 0, out.stderr
    body = last_json(out.stdout)
    assert body["state"] == "succeeded" and body["summary"] == "built ok" and body["id"] != jid


# ── 5. SIGTERM — 도는 잡은 lost(`worker stopped`) · 워커 종료 0 ────────────────


def test_sigterm_stops_the_worker_gracefully_and_reports_the_job_lost(stack, tree):
    worker = stack.start()
    jid = stack.submit(tree, "slowl")
    stack.wait_state(jid, ("running",), 15)
    pid = stack.wait_pid(jid)
    t0 = time.monotonic()
    worker.signal(signal.SIGTERM)
    rc = worker.wait_exit(GRACE + 5)
    took = time.monotonic() - t0
    assert rc == 0, (rc, worker.log_text())
    fin = stack.wait_terminal(jid, timeout=5)  # finish 는 종료 전에 보냈어야 한다
    assert fin["state"] == "lost" and fin["summary"] == "worker stopped", fin
    assert took <= GRACE + 5, took
    wait_until(lambda: is_dead(pid), 5, f"job process {pid} to die")
    assert lane_view(stack.status()) == [(LANE, "idle", None)]  # 레인 정본은 DB — 잡이 없다


# ── 6. git_ref — 워커가 자기 [[repos]] 로 bare 레포에서 fetch ─────────────────


@needs_git
def test_git_ref_job_is_fetched_by_the_worker_from_its_own_repos(make_stack, tmp_path):
    remote = build_remote(tmp_path)
    st = make_stack(repo_url=remote.url)
    worker_toml = tmp_path / "worker.toml"
    worker_toml.write_text(WORKER_TOML.format(url=remote.url))
    st.start(worker_config=worker_toml)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    with StatusWatcher(st) as watch:
        out = rcm("run", "deploy", "--ref", "main", "--timeout", "30", env=st.env, cwd=cwd)
    assert out.returncode == 0, out.stderr
    body = last_json(out.stdout)
    jid = body["id"]
    assert body["state"] == "succeeded" and body["exit_code"] == 0, body
    assert body["source"]["mode"] == "git_ref" and body["source"]["sha"] == remote.main
    assert watch.busy(jid), watch.seen
    log = rcm("logs", str(jid), env=st.env).stdout
    assert HELLO in log and f"HEAD={remote.main}\n" in log and "RCM_REF=main\n" in log
    # 서버는 자재화하지 않았고(ls-remote 만), 워커는 성공한 워크스페이스를 지웠다(§2)
    assert not (st.server.data_dir / "workspaces" / str(jid)).exists()
    assert not (st.worker_data / "workspaces" / str(jid)).exists()


# ── 7. 서버 재시작 — 원격 잡은 running 으로 남고 finish 가 들어가면 succeeded ──


def test_server_restart_keeps_the_remote_job_running_until_the_worker_finishes(stack, tree):
    stack.start()
    jid = stack.submit(tree, "slow4")
    stack.wait_state(jid, ("running",), 15)
    stack.wait_pid(jid)  # 스크립트가 진짜 돌기 시작했다(sleep 4 → 마커)
    t0 = time.monotonic()
    stack.server.stop(signal.SIGTERM, timeout=10)
    stack.server.start()
    gap = time.monotonic() - t0
    assert gap < 5, gap  # 워커의 보고 재시도 창(1·2·4초) 안에 다시 떴다
    # 재시작 직후: 원격 잡은 lost 가 아니라 아직 running(recover_on_start 는 로컬 잡만)
    view = stack.job(jid)
    assert view["state"] == "running", view
    fin = stack.wait_terminal(jid, timeout=25)
    assert fin["state"] == "succeeded" and fin["summary"] == "built ok", fin
    prog = stack.progress(jid)
    assert (prog.steps_total, prog.steps_done) == (2, 2)
    # 워커는 서버 재시작을 넘겨 살아 있고(heartbeat 이 다시 붙는다) 다음 잡도 받는다
    assert lane_view(stack.status()) == [(LANE, "idle", None)]
    out = rcm("run", "lin", "--timeout", "30", env=stack.env, cwd=tree)
    assert out.returncode == 0 and last_json(out.stdout)["state"] == "succeeded", out.stderr
