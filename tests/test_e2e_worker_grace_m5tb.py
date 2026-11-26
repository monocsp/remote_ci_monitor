"""끊김 유예 · 자기 멈춤 · 펜싱 · 감독자 · 그룹 홀드의 e2e(M5t-b §7.1 의 e2e 몫).

실제 `rcm serve` 와 `rcm worker` 프로세스를 띄운다(`tests/test_e2e_worker.py` 의 하네스). 워커는
서버 대신 TCP 중계(`tests/tcp_relay.py`)에 붙을 수 있다. 중계를 멈추면 분단이고, 서버 정지는 실제
서버를 멈춰서 만든다. 시험 서버는 G = 90 · `worker_timeout_seconds` = 10 · heartbeat 1초다
(§7.5). 표식 잡의 감시 스레드는 마지막 2xx 의 `G − m`(m = 15 + grace 2 + 10 = 27), 곧 63초 뒤에
멈춘다.

G 만료를 실제로 기다리는 시험과 20초가 넘는 시험은 `@pytest.mark.slow` 다. 시간 경계에 기대는 단언은
`-n auto` 부하를 견디게 여유를 둔다(멈춤은 「그 전에 이미」, 생존은 「몇 초 뒤에도」로 본다).
"""

from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from tcp_relay import TcpRelay
from test_e2e_worker import (
    CLIENT,
    PID_LINE,
    POLL,
    TERMINAL,
    E2EServer,
    WorkerProc,
    is_dead,
    kill_group,
    last_json,
    rcm,
    remote_lanes,
    wait_until,
)

G = 90
SELF_STOP = G - (15 + 2 + 10)  # 63 — 감시 스레드의 문턱(§4.3)
LONG = f"{PID_LINE}; exec sleep 400"
SHORT = f"{PID_LINE}; sleep 22"
DAEMON_PY = """\
import os, sys, time
mark = sys.argv[1]
if os.fork() == 0:
    os.setsid()  # 세션을 빠져나간 자손 — rcm 은 볼 수 없다(RL49)
    with open(mark, "w") as f:
        f.write(str(os.getpid()))
    time.sleep(120)
    os._exit(0)
time.sleep(0.5)
"""
EXTRA_TOML = """
[[presets]]
name = "ship"
pool = "linux"
irreversible = true
argv = ["sh", "-c", '{long}']
timeout_seconds = 900
[presets.env]
RCM_MARK_DIR = "{marks}"

[[presets]]
name = "plain"
pool = "linux"
argv = ["sh", "-c", '{long}']
timeout_seconds = 900
[presets.env]
RCM_MARK_DIR = "{marks}"

[[presets]]
name = "shipshort"
pool = "linux"
irreversible = true
argv = ["sh", "-c", '{short}']
timeout_seconds = 120
[presets.env]
RCM_MARK_DIR = "{marks}"

[[presets]]
name = "plainshort"
pool = "linux"
argv = ["sh", "-c", '{short}']
timeout_seconds = 120
[presets.env]
RCM_MARK_DIR = "{marks}"

[[presets]]
name = "fails"
pool = "linux"
argv = ["sh", "-c", "sleep 0.3; exit 1"]
timeout_seconds = 60

[[presets]]
name = "tmo"
pool = "linux"
argv = ["sh", "-c", "sleep 30"]
timeout_seconds = 1

[[presets]]
name = "daemon"
pool = "linux"
irreversible = true
argv = ["{python}", "{daemon}", "{marks}/daemon.pid"]
timeout_seconds = 60
"""


class GWorker(WorkerProc):
    """이름이 있는 워커 — 중계를 거칠 수 있다."""

    def __init__(self, server: E2EServer, name: str, token: str, tmp: Path, url: str, n: int):
        super().__init__(server, token, tmp / f"wdata-{name}-{n}", tmp / f"worker-{name}-{n}.log")
        self.name = name
        self.argv[self.argv.index("--server") + 1] = url

    def wait_registered(self, client_token: str, timeout: float) -> None:
        assert self.proc is not None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise AssertionError(
                    f"{self.name} exited {self.proc.returncode}:\n{self.log_text()}"
                )
            try:
                _, doc = self.server.get("/api/status", client_token)
            except OSError:
                doc = None
            if doc and any(
                w["worker"] == self.name and w["state"] in ("idle", "busy")
                for w in remote_lanes(doc)
            ):
                return
            time.sleep(POLL * 2)
        raise AssertionError(f"{self.name} did not register:\n{self.log_text()}")


class Grace:
    """서버 하나 + 중계 하나 + 이름 있는 워커들."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.marks = tmp / "marks"
        self.marks.mkdir()
        self.server = E2EServer(tmp, marks=self.marks)
        daemon = tmp / "daemon.py"
        daemon.write_text(DAEMON_PY)
        with self.server.config.open("a") as f:
            f.write(
                EXTRA_TOML.format(
                    long=LONG,
                    short=SHORT,
                    marks=self.marks,
                    python=sys.executable,
                    daemon=daemon,
                )
            )
        self.client = self.server.token(CLIENT)
        self.admin = self.server.token("ops", admin=True)
        self.tokens = {n: self.server.token(n, worker=True) for n in ("build-02", "build-03")}
        self.env = self.server.client_env(self.client)
        self.relay: TcpRelay | None = None
        self.workers: list[GWorker] = []
        self.tree = tmp / "tree"
        self.tree.mkdir()
        (self.tree / "README.md").write_text("grace\n")

    def start(self, *names: str, relay: bool = False) -> list[GWorker]:
        self.server.start()
        if relay:
            self.relay = TcpRelay(self.server.port)
        return [self.spawn(n) for n in names]

    def spawn(self, name: str, *, direct: bool = False) -> GWorker:
        url = self.relay.url if self.relay is not None and not direct else self.server.url
        w = GWorker(self.server, name, self.tokens[name], self.tmp, url, len(self.workers))
        self.workers.append(w)
        w.start(self.client)
        return w

    def submit(self, preset: str) -> int:
        out = rcm("run", preset, "--no-wait", "--no-join", env=self.env, cwd=self.tree)
        assert out.returncode == 0, out.stderr
        return int(last_json(out.stdout)["job_id"])

    def job(self, jid: int) -> dict[str, Any]:
        status, doc = self.server.get(f"/jobs/{jid}", self.client)
        assert status == 200, (status, doc)
        return doc

    def status(self) -> dict[str, Any]:
        status, doc = self.server.get("/api/status", self.client)
        assert status == 200, (status, doc)
        return doc

    def holds(self) -> list[int]:
        return [h["job_id"] for h in self.status()["irreversible_holds"]]

    def wait_state(self, jid: int, states: tuple[str, ...], timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        last: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            try:
                last = self.job(jid)
            except (OSError, AssertionError):
                last = None
            if last is not None and last["state"] in states:
                return last
            time.sleep(POLL * 2)
        raise AssertionError(f"job #{jid} is {last and last['state']}, wanted {states}")

    def pid(self, jid: int, timeout: float = 20) -> int:
        path = self.marks / f"{jid}.pid"
        wait_until(lambda: path.exists() and path.read_text().strip(), timeout, path.name)
        return int(path.read_text().strip())

    def wait_exit(self, jid: int, timeout: float = 30) -> int:
        out = rcm(
            "wait", "--job", str(jid), "--timeout", str(timeout), env=self.env, timeout=timeout + 30
        )
        return out.returncode

    def teardown(self) -> None:
        for w in self.workers:
            w.kill()
        if self.relay is not None:
            self.relay.close()
        self.server.kill()
        for p in self.marks.glob("*.pid"):
            text = p.read_text().strip()
            if text.isdigit():
                kill_group(int(text))
                try:
                    os.kill(int(text), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass


@pytest.fixture
def grace(tmp_path: Path):
    g = Grace(tmp_path)
    yield g
    g.teardown()


def alive(pid: int) -> bool:
    return not is_dead(pid)


# ── RL6 기준선 ────────────────────────────────────────────────────────────────


def test_rl6_a_connected_remote_jobs_keep_their_exit_codes(grace):
    grace.start("build-02")
    ok = grace.submit("lin")
    assert grace.wait_exit(ok) == 0
    bad = grace.submit("fails")
    assert grace.wait_exit(bad) == 1
    slow = grace.submit("tmo")
    assert grace.wait_exit(slow) == 2
    cancel = grace.submit("plain")
    grace.pid(cancel)
    assert rcm("cancel", str(cancel), env=grace.env).returncode == 0
    assert grace.wait_exit(cancel) == 2
    for jid, state in (
        (ok, "succeeded"),
        (bad, "failed"),
        (slow, "timed_out"),
        (cancel, "cancelled"),
    ):
        assert grace.job(jid)["state"] == state
    assert grace.holds() == []


@pytest.mark.slow
def test_rl6_b_a_partition_past_the_worker_timeout_but_inside_g_keeps_both_jobs(grace):
    grace.start("build-02", "build-03", relay=True)
    a = grace.submit("shipshort")
    b = grace.submit("plainshort")
    pa, pb = grace.pid(a), grace.pid(b)
    grace.relay.pause()
    time.sleep(16)  # worker_timeout 10 + 첫 janitor 틱이 지나도
    assert grace.job(a)["state"] == "running" and grace.job(b)["state"] == "running"
    assert alive(pa) and alive(pb)
    grace.relay.resume()
    assert grace.wait_exit(a, 60) == 0 and grace.wait_exit(b, 60) == 0  # 같은 세션으로 이어 보고
    assert grace.job(a)["state"] == "succeeded" and grace.job(b)["state"] == "succeeded"
    assert grace.holds() == []


# ── RL1 · RL5 · RL42 · RL3 — 분단이 G 를 넘는다 ─────────────────────────────────


@pytest.mark.slow
def test_rl1_rl5_rl42_rl3_a_partition_past_g(grace):
    w2, w3 = grace.start("build-02", "build-03", relay=True)
    x = grace.submit("ship")
    px = grace.pid(x)
    y = grace.submit("plain")
    py = grace.pid(y)
    grace.relay.pause()
    cut = time.monotonic()
    # 표식 잡은 감시 스레드가 G − m 에 스스로 멈춘다 — 서버가 닫기 전에 이미 죽어 있다
    wait_until(lambda: is_dead(px), SELF_STOP + 20, f"the irreversible job pid {px} to stop")
    stopped = time.monotonic() - cut
    assert stopped < G - 5, stopped
    assert grace.job(x)["state"] == "running"  # 서버는 아직 G 를 기다린다
    lx = grace.wait_state(x, TERMINAL, G + 30)
    ly = grace.wait_state(y, TERMINAL, 30)
    assert lx["state"] == ly["state"] == "lost"
    assert lx["summary_code"] == ly["summary_code"] == "worker_unreachable"
    assert alive(py)  # RL5 — 일반 잡은 워커가 죽이지 않는다
    assert grace.holds() == [x]  # RL42 — 표식 잡만 홀드, 일반 잡은 0
    z = grace.submit("ship")  # 같은 그룹 — 홀드에 막힌다
    time.sleep(3)
    assert grace.job(z)["state"] == "queued" and grace.job(z)["reason"] == "needs_action"
    # 서버를 다시 띄워도 홀드는 그대로다
    grace.server.stop()
    grace.server.start()
    time.sleep(6)
    assert grace.holds() == [x] and grace.job(z)["state"] == "queued"
    # 연결이 돌아오면 워커의 finish(group_gone: true)가 홀드를 푼다 — 그 뒤에 z 가 시작한다
    grace.relay.resume()
    wait_until(lambda: grace.holds() == [], 60, "the returning worker to release the hold")
    pz = grace.pid(z, 60)
    assert is_dead(px)  # RL3 — 둘째 잡이 시작할 때 첫 잡의 프로세스는 이미 없다
    assert grace.job(x)["state"] == "lost"  # 늦은 보고가 결과를 바꾸지 않는다
    assert grace.wait_exit(x, 10) == 3
    time.sleep(3)
    assert alive(py)  # 재연결 뒤 409 job_closed 도 일반 잡을 죽이지 않는다
    kill_group(pz)
    del w2, w3


# ── RL2 · RL7(b) — 서버 정지가 G 를 넘는다 ──────────────────────────────────────


@pytest.mark.slow
def test_rl2_rl7b_a_server_stop_past_g(grace):
    grace.start("build-02", "build-03")
    x = grace.submit("ship")
    px = grace.pid(x)
    y = grace.submit("plain")
    py = grace.pid(y)
    grace.server.stop()
    down = time.monotonic()
    wait_until(
        lambda: is_dead(px), SELF_STOP + 20, "the irreversible job to stop while the server is down"
    )
    assert alive(py)
    time.sleep(max(0.0, G + 3 - (time.monotonic() - down)))
    grace.server.start()
    # 꺼져 있던 동안은 유예로 세지 않는다 — 기동 직후 틱이 일반 잡을 닫지 않는다
    time.sleep(7)
    assert grace.job(y)["state"] == "running"
    lx = grace.wait_state(x, TERMINAL, 60)
    assert lx["state"] == "lost" and lx["summary_code"] == "worker_self_stopped"
    assert lx.get("failed_step") is None and lx.get("last_step") is None
    assert grace.wait_exit(x, 10) == 3
    assert grace.holds() == []  # 워커의 finish 가 group_gone: true 를 실었다
    assert alive(py)


# ── RL10 — 같은 토큰의 둘째 프로세스 ────────────────────────────────────────────


def test_rl10_a_second_process_fences_the_first_which_stops_its_job_and_exits_zero(grace):
    (first,) = grace.start("build-02")
    x = grace.submit("ship")
    px = grace.pid(x)
    grace.spawn("build-02", direct=True)  # 같은 토큰 · 다른 data 폴더
    assert first.wait_exit(30) == 0
    assert is_dead(px)
    lx = grace.wait_state(x, TERMINAL, 30)
    assert lx["state"] == "lost" and lx["summary_code"] == "worker_replaced"
    assert grace.wait_exit(x, 10) == 3
    assert grace.holds() == []  # 좁은 확인의 group_gone: true


# ── RL43 — 워커 kill -9 ──────────────────────────────────────────────────────


def test_rl43_killing_the_worker_ends_the_irreversible_command_but_not_a_plain_one(grace):
    w2, w3 = grace.start("build-02", "build-03")
    x = grace.submit("ship")
    px = grace.pid(x)
    y = grace.submit("plain")
    py = grace.pid(y)
    for w in (w2, w3):
        assert w.proc is not None
        os.kill(w.proc.pid, signal.SIGKILL)  # 워커 프로세스만 — 그룹은 그대로
        w.proc.wait(timeout=10)
    wait_until(lambda: is_dead(px), 5, "the supervisor to end the irreversible command")
    time.sleep(2)
    assert alive(py)  # (e) 감독자가 없는 일반 잡은 워커가 죽어도 산다(결정 253)


# ── RL49 — 세션을 빠져나간 자손 ──────────────────────────────────────────────────


def test_rl49_a_setsid_descendant_survives_and_the_group_is_gone_so_no_hold(grace):
    grace.start("build-02")
    jid = grace.submit("daemon")
    assert grace.wait_exit(jid, 60) == 0
    mark = grace.marks / "daemon.pid"
    wait_until(lambda: mark.exists() and mark.read_text().strip(), 10, "the daemon pid")
    pid = int(mark.read_text())
    try:
        assert alive(pid)  # rcm 은 그 자손을 끊지 않는다(볼 수 없다 — 문서가 말한다)
        assert grace.holds() == []
    finally:
        os.kill(pid, signal.SIGKILL)


# ── RL50 — claim 을 가로지른 heartbeat 가 늦게 닿는다 ─────────────────────────────


@pytest.mark.slow
def test_rl50_a_heartbeat_sent_before_the_claim_answer_lands_after_it_without_harm(grace):
    """워커가 놀며 claim 을 기다리는 동안 찍은 heartbeat H(`jobs: []`, `claims_settled` 는 열린
    claim 앞)를 중계가 붙잡는다. 그 뒤 같은 경로의 heartbeat 는 끊는다 — H 가 서버에 닿는 가장 새
    seq 다. 그사이 X 가 claim 되어 돈다. H 가 늦게 닿아도 X 를 닫지 않는다: H 의 `claims_settled` 는
    X 를 돌려준 claim 보다 작다(T5, 결정 339-a). 변이 `t5-ignores-claims-settled` 를 넣으면 H 가
    X 를 `lost` 로 닫아 빨갛다.

    `hb-stale-seq-judged` 하나만으로는 진짜 워커가 빨강을 만들 수 없다 — 진짜 워커의 낡은 heartbeat
    은 목록에 없는 잡의 claim 을 늘 「아직 안 끝남」으로 싣는다(R20). 그 변이는 인위적인
    `claims_settled` 를 싣는 `tests/test_hb_order_m5tb.py` 의 시험이 잡는다."""
    grace.start("build-02", relay=True)
    grace.relay.hold_next("/worker/heartbeat", refuse_rest=True)
    assert grace.relay.wait_held(10)  # 잡이 없을 때 찍은 H — X 의 claim 응답보다 앞이다
    x = grace.submit("ship")
    px = grace.pid(x)
    wait_until(lambda: grace.relay.refused >= 1, 30, "a later heartbeat to be refused")
    grace.relay.release_held()
    time.sleep(2)  # H 가 먼저 닿아 판정된다 — 그 뒤에 다른 heartbeat 를 흘린다
    grace.relay.stop_refusing()
    time.sleep(3)
    assert grace.job(x)["state"] == "running" and alive(px)
    assert grace.holds() == []
    second = grace.submit("ship")
    time.sleep(3)
    assert grace.job(second)["state"] == "queued"  # 같은 그룹 — 두 프로세스가 같이 살지 않는다
    assert grace.holds() == []
