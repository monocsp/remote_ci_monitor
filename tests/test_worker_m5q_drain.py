"""원격 워커가 돌다가 서버 버전이 바뀌면 드레인한다 (M5q PR B2).

`docs/m5q-test-scenarios-b.md` 의 drain 줄이다.

명세는 `docs/m5q-workplan.md` §3.2 ③ · ④ · §3.3 · §5.4 · §5.5 · §14.9 다. §14 가 앞 절을 이긴다.
드레인은 `stopping` 과 다른 상태다. 레인은 새 claim 을 하지 않고 기다리되 이미 받은 잡은 끝까지 돌려
보고한다. 모든 레인이 잡 없이 기다리면 `run()` 이 `stopping` 을 켜고 `restart_for` 를 남긴다.
드레인 중에 서버가 자기 버전으로 돌아오면 드레인을 취소하고, 세 번째 버전이 오면 목표를 바꾼다.

같은 프로세스의 진짜 `RemoteWorker` 가 진짜 서버(`WorkerServer`)와 HTTP 로 말한다. 서버 버전은
`srv.app.version` 을 바꿔 올린다(wheel 은 이 파일에서 쓰지 않는다). 요청은 `App` 의 처리 함수를
감싸 세고, 그때 워커의 `draining` 값을 함께 적는다. 잡을 붙잡는 프리셋 `hold` 는 `go.<잡 번호>`
파일이 생길 때까지 돌고, SIGTERM 을 받으면 `term.<잡 번호>` 를 남긴다.

시험을 먼저 썼다 — 구현 전에는 빨갛다.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from remote_ci_monitor import __version__, follow, hostsample, remote_worker
from remote_ci_monitor.config import Probe, WorkerConfig, parse_preset
from remote_ci_monitor.core.model import LOST, QUEUED, SUCCEEDED
from remote_ci_monitor.remote_worker import RemoteWorker
from remote_ci_monitor.server import ApiError
from test_cli_worker import WORKER, make_server
from test_server import sh

NEW = "99.0.1"
NEW2 = "99.0.2"
MALFORMED = "ignoring a malformed server_version from the server"
DRAINING = "draining: no new claims, restarting when running jobs finish"


def drain_line(version: str, lane: int | None = None) -> str:
    head = f"lane {lane}: " if lane is not None else ""
    return f"{head}server runs v{version} — {DRAINING}"


class Stamped(threading.Event):
    """`set()` 한 시각(monotonic)을 남기는 Event — 드레인과 정지의 순서를 본다."""

    def __init__(self) -> None:
        super().__init__()
        self.set_at: float | None = None

    def set(self) -> None:
        if self.set_at is None:
            self.set_at = time.monotonic()
        super().set()


class Clock:
    """`RemoteWorker.now_fn` 에 넣는 시계. 지금에서 출발해 `advance` 로만 민다."""

    def __init__(self) -> None:
        self.now = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)

    def epoch(self) -> float:
        return self.now.timestamp()


class Rig:
    """진짜 서버 하나와 그 서버를 보는 같은 프로세스의 워커 하나."""

    def __init__(self, tmp: Path, *, claim_wait: int = 1):
        self.tmp = tmp
        self.marks = tmp / "marks"
        self.marks.mkdir()
        self.srv = make_server(
            tmp, worker_heartbeat_seconds=1, worker_claim_wait_seconds=claim_wait
        )
        m = str(self.marks)
        hold = (
            f"trap 'touch {m}/term.$RCM_JOB_ID; exit 143' TERM; touch {m}/started.$RCM_JOB_ID; "
            f"while [ ! -e {m}/go.$RCM_JOB_ID ]; do sleep 0.05; done"
        )
        self.srv.cfg.presets = (
            *self.srv.cfg.presets,
            parse_preset(sh("hold", hold, pool="linux")),
        )
        self.url = f"http://127.0.0.1:{self.srv.port}"
        self.logs: list[str] = []
        self.claims: list[tuple[float, bool]] = []  # 서버가 받은 claim (시각, 그때 draining)
        # 워커가 claim 을 보내기로 한 때(시각, 그때 draining). 서버가 받은 시각은 heartbeat 와
        # 경주한다 — 드레인 직전에 떠난 claim 은 받아도 된다(G1)
        self.sent: list[tuple[float, bool]] = []
        self.heartbeats: list[tuple[float, Any]] = []  # (시각, 본문의 jobs)
        self.finishes: list[tuple[float, int, int]] = []  # (시각, 잡, 상태 코드)
        self.forge: Any = None  # heartbeat 응답을 바꾸는 함수
        self.finish_fails = 0  # 앞에서부터 이만큼의 finish 를 503 으로
        self.down = False  # 참이면 heartbeat 와 finish 가 503 — 서버를 갈아 끼우는 몇 초
        self.rw: RemoteWorker | None = None
        self.out: dict[str, Any] = {}
        self.thread: threading.Thread | None = None
        self._wrap()

    def _wrap(self) -> None:
        app = self.srv.app
        claim, heartbeat, finish = app.worker_claim, app.worker_heartbeat, app.worker_finish
        rig = self

        def worker_claim(token, body):
            rig.claims.append((time.monotonic(), rig.draining()))
            return claim(token, body)

        def worker_heartbeat(token, body):
            if rig.down:
                raise ApiError(503, "restarting")
            out = heartbeat(token, body)
            rig.heartbeats.append((time.monotonic(), (body or {}).get("jobs")))
            if rig.forge is not None:
                out = rig.forge(dict(out))
            return out

        def worker_finish(token, job_id, body):
            if rig.down:
                rig.finishes.append((time.monotonic(), job_id, 503))
                raise ApiError(503, "restarting")
            if rig.finish_fails > 0:
                rig.finish_fails -= 1
                rig.finishes.append((time.monotonic(), job_id, 503))
                raise ApiError(503, "busy")
            out = finish(token, job_id, body)
            rig.finishes.append((time.monotonic(), job_id, 200))
            return out

        app.worker_claim = worker_claim  # type: ignore[method-assign]
        app.worker_heartbeat = worker_heartbeat  # type: ignore[method-assign]
        app.worker_finish = worker_finish  # type: ignore[method-assign]

    def draining(self) -> bool:
        ev = getattr(self.rw, "draining", None)
        return bool(ev is not None and ev.is_set())

    def worker(self, *, lanes: int = 2, **kw: Any) -> RemoteWorker:
        cfg = WorkerConfig(
            server=self.url,
            token=self.srv.tokens[WORKER],
            pool="linux",
            lanes=lanes,
            data_dir=str(self.tmp / "w"),
            grace_seconds=2,
        )
        kw.setdefault("follows", True)
        self.rw = RemoteWorker(cfg, log=self.logs.append, **kw)
        self.rw.draining = Stamped()
        self.rw.stopping = Stamped()
        real_claim = self.rw.client.claim

        def claim(*a: Any, **k: Any) -> Any:
            self.sent.append((time.monotonic(), self.draining()))
            return real_claim(*a, **k)

        self.rw.client.claim = claim  # type: ignore[method-assign]
        return self.rw

    def start(self, **kw: Any) -> RemoteWorker:
        rw = self.rw or self.worker(**kw)
        self.thread = threading.Thread(target=lambda: self.out.update(code=rw.run()), daemon=True)
        self.thread.start()
        self.until(lambda: self.srv.store.get_worker(WORKER) is not None, "registered")
        return rw

    def join(self, timeout: float = 30) -> Any:
        assert self.thread is not None
        self.thread.join(timeout)
        assert not self.thread.is_alive(), self.logs
        return self.out.get("code")

    def until(self, cond: Any, what: str, timeout: float = 20) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cond():
                return
            time.sleep(0.05)
        raise AssertionError(f"timed out waiting for {what}: {self.logs}")

    def bump(self, version: str) -> None:
        self.srv.app.version = version

    def hold(self) -> int:
        jid = self.srv.queued_job(preset="hold")
        self.until(lambda: (self.marks / f"started.{jid}").exists(), f"#{jid} started")
        return jid

    def go(self, jid: int) -> None:
        (self.marks / f"go.{jid}").touch()

    def term(self, jid: int) -> bool:
        return (self.marks / f"term.{jid}").exists()

    def forget(self) -> None:
        """서버 DB 에서 워커 행을 지운다 — DB 를 백업으로 되돌린 경우(④ 의 준비)."""
        self.srv.store._conn().execute("DELETE FROM workers WHERE name=?", (WORKER,))

    def state(self, jid: int) -> Any:
        return self.srv.store.get_job(jid)

    def count(self, text: str) -> int:
        return sum(1 for ln in self.logs if text in ln)

    def close(self) -> None:
        if self.rw is not None:
            self.rw.stop()
            for p in self.marks.glob("started.*"):
                self.go(int(p.name.split(".")[1]))
        if self.thread is not None:
            self.thread.join(20)
        self.srv.close()


@pytest.fixture
def rig(tmp_path):
    r = Rig(tmp_path)
    yield r
    r.close()


# ── ③ heartbeat 의 server_version ──────────────────────────────────────────


def test_drain_with_no_running_job_restarts_for_the_new_version(rig):
    # B7 · W5: heartbeat 의 다른 server_version 을 보고 드레인한다. 드레인 뒤에 보낸 claim 이 없고
    # `stopping` 은 드레인보다 뒤에 켜진다. `run()` 은 0 이고 `restart_for` 가 서버 버전이다
    rw = rig.start()
    rig.until(lambda: len(rig.claims) >= 2, "both lanes polling")
    rig.bump(NEW)
    code = rig.join()
    assert code == 0, rig.logs
    assert rw.restart_for == NEW
    assert rw.draining.set_at is not None and rw.stopping.set_at is not None
    assert rw.draining.set_at < rw.stopping.set_at
    assert rw.stopping.set_at - rw.draining.set_at < 1 + 2 + 1  # claim_wait + 2초 + 여유
    assert [c for c in rig.sent if c[1]] == []
    assert rig.count(drain_line(NEW)) == 1, rig.logs
    assert "stopped" not in rig.logs  # #234 격리 검증: 드레인 끝은 멈춤이 아니라 재실행이다


def test_drain_lets_the_running_job_finish_and_report(rig):
    # B9 · W6 · G3: 도는 잡은 SIGTERM 없이 끝까지 가고 원래 결과로 보고된다. 드레인 동안 heartbeat
    # 는 계속 돌며 그 잡을 싣는다. 드레인 뒤에 들어온 잡은 집지 않는다. 보고가 끝난 뒤에 정지한다
    rw = rig.start()
    jid = rig.hold()
    rig.bump(NEW)
    rig.until(rig.draining, "draining")
    # 드레인 직전에 들어간 long-poll 은 잡을 받아도 된다(G1). 그 long-poll 이 끝나 빈 레인이
    # 기다리는 상태가 된 뒤에 넣은 잡은 집지 않는다
    rig.until(lambda: rw._idle_lanes, "the idle lane waits")
    queued = rig.srv.queued_job(preset="lin")
    beats_before = len(rig.heartbeats)
    time.sleep(3)
    assert not rw.stopping.is_set()
    assert not rig.term(jid)
    assert len(rig.heartbeats) - beats_before >= 2
    assert all(jid in (jobs or []) for _, jobs in rig.heartbeats[beats_before:])
    rig.go(jid)
    assert rig.join() == 0, rig.logs
    job = rig.state(jid)
    assert job.state == SUCCEEDED and job.exit_code == 0, (job.state, job.summary)
    assert job.summary != remote_worker.STOP_SUMMARY
    assert not rig.term(jid)
    done = [at for at, j, status in rig.finishes if j == jid and status == 200]
    assert done and done[0] < rw.stopping.set_at
    assert rig.state(queued).state == QUEUED
    assert [c for c in rig.sent if c[1]] == []
    assert rw.restart_for == NEW


def test_long_poll_job_arriving_during_the_drain_is_run_then_restarts(tmp_path):
    # G1 · W14: 드레인 직전에 들어간 long-poll 이 잡을 돌려주면 그 잡을 끝까지 돌리고 보고한 뒤에
    # 재실행한다. 드레인 뒤에 시작한 claim 은 없다
    r = Rig(tmp_path, claim_wait=5)
    try:
        rw = r.worker(lanes=1)
        r.start()
        r.until(lambda: len(r.claims) >= 1, "lane in long-poll")
        time.sleep(0.3)
        r.bump(NEW)
        rw.heartbeat_once()
        assert rw.draining.is_set()
        jid = r.srv.queued_job(preset="hold")
        r.until(lambda: (r.marks / f"started.{jid}").exists(), "long-poll job started")
        r.go(jid)
        assert r.join() == 0, r.logs
        assert r.state(jid).state == SUCCEEDED
        assert [c for c in r.sent if c[1]] == []
        assert rw.restart_for == NEW
    finally:
        r.close()


def test_first_heartbeat_can_start_the_drain(rig, monkeypatch):
    # G5: 등록 직후 서버가 바뀌면 `run()` 의 동기 heartbeat 가 드레인을 켠다. 레인은 claim 없이
    # 바로 기다리고 재실행한다
    real = rig.srv.app.worker_register

    def register(token, body):
        out = real(token, body)
        rig.bump(NEW)
        return out

    monkeypatch.setattr(rig.srv.app, "worker_register", register)
    rw = rig.start()
    assert rig.join() == 0, rig.logs
    assert rig.claims == []
    assert rw.restart_for == NEW


def test_drain_is_cancelled_when_the_server_comes_back(rig):
    # G6 · §14 B질문8: 드레인 중에 서버가 지금 도는 버전으로 돌아오면 드레인을 취소한다. 레인은
    # 다시 claim 하고 재실행은 없다
    rw = rig.start(lanes=1)
    jid = rig.hold()
    rig.bump(NEW)
    rig.until(rig.draining, "draining")
    rig.bump(__version__)
    rig.until(lambda: not rw.draining.is_set(), "drain cancelled")
    assert rig.count(f"server runs v{__version__} again — drain cancelled") == 1, rig.logs
    rig.go(jid)
    rig.until(lambda: rig.state(jid).state == SUCCEEDED, "job reported")
    nxt = rig.srv.queued_job(preset="lin")
    rig.until(lambda: rig.state(nxt).state == SUCCEEDED, "next job claimed after the cancel")
    assert not rw.stopping.is_set()
    rw.stop()
    assert rig.join() == 0
    assert rw.restart_for is None


def test_third_version_replaces_the_target(rig):
    # G7 · §14 B질문8: 드레인 중에 세 번째 버전이 오면 `follow_to` 를 마지막 값으로 바꾼다
    rw = rig.start(lanes=1)
    jid = rig.hold()
    rig.bump(NEW)
    rig.until(rig.draining, "draining")
    assert rw.follow_to == NEW
    rig.bump(NEW2)
    rig.until(lambda: rw.follow_to == NEW2, "new target")
    assert rig.count(f"server now runs v{NEW2} — still draining") == 1, rig.logs
    rig.go(jid)
    assert rig.join() == 0
    assert rw.restart_for == NEW2


def test_paused_server_still_drains(rig):
    # G17: 서버가 일시정지돼 레인이 claim 을 쉬던 중에도 드레인은 끝나 재실행한다
    rw = rig.start()
    rig.srv.store.set_paused("admin", rig.srv.clock())
    rig.until(lambda: rw.paused, "worker saw the pause")
    rig.bump(NEW)
    assert rig.join(10) == 0, rig.logs
    assert rw.restart_for == NEW


def test_old_server_without_server_version_does_nothing(rig):
    # W10: heartbeat 에 `server_version` 이 없는 옛 서버 — 아무 일도 없다
    rw = rig.worker()
    rw.register()
    rig.bump(NEW)
    rig.forge = lambda out: {k: v for k, v in out.items() if k != "server_version"}
    for _ in range(3):
        assert rw.heartbeat_once()
    assert not rw.draining.is_set()
    assert rig.logs == []


@pytest.mark.parametrize("bad", ["../x", "0.8.1/../../x", "1" * 65, 7, "0.8.1\n", None])
def test_malformed_server_version_is_ignored(rig, bad):
    # E6 · §14.1: 형식이 틀린 값은 없는 것으로 본다. 로그는 프로세스당 한 번이고 값은 찍지 않는다.
    # `null` 은 키가 없는 것과 같다
    rw = rig.worker()
    rw.register()
    rig.forge = lambda out: {**out, "server_version": bad}
    for _ in range(3):
        assert rw.heartbeat_once()
    assert not rw.draining.is_set()
    assert rw.follow_to is None
    assert rig.count(MALFORMED) == (0 if bad is None else 1), rig.logs
    assert not any("../x" in ln for ln in rig.logs)


def test_same_version_string_does_not_drain(rig):
    # G28: 문자열이 같으면 드레인하지 않는다(sha 는 보지 않는다)
    rw = rig.worker()
    rw.register()
    for _ in range(3):
        assert rw.heartbeat_once()
    assert not rw.draining.is_set()


# ── 300초 창 (§3.3 고리 막기 · §14 B질문9) ──────────────────────────────────


def test_a_drain_inside_the_window_is_suppressed_until_it_ends(rig, monkeypatch):
    # E7 · W8: 재실행 60초 뒤의 ③ 은 드레인하지 않고 로그를 한 번 쓴다. 그동안 잡은 계속 흐른다.
    # 창이 끝나면(재실행 뒤 301초) 다음 heartbeat 에서 드레인한다
    clock = Clock()
    monkeypatch.setenv(follow.ENV_WORKER_RESTARTED_AT, str(int(clock.epoch()) - 60))
    rw = rig.worker(now_fn=clock)
    rig.start()
    rig.bump(NEW)
    rig.until(lambda: rig.count("not draining") >= 1, "suppressed")
    jid = rig.srv.queued_job(preset="lin")
    rig.until(lambda: rig.state(jid).state == SUCCEEDED, "a job still flows")
    beats = len(rig.heartbeats)
    rig.until(lambda: len(rig.heartbeats) >= beats + 3, "more heartbeats")
    assert not rw.draining.is_set()
    assert rig.count(f"server runs v{NEW} — not draining: restarted 60s ago") == 1, rig.logs
    clock.advance(241)
    assert rig.join() == 0, rig.logs
    assert rw.restart_for == NEW


@pytest.mark.parametrize(
    ("raw", "drains"),
    [("abc", False), ("", False), ("-1", True), ("future", False), (None, True)],
)
def test_restarted_at_values(rig, monkeypatch, raw, drains):
    # E8 · §14 B질문9: 숫자가 아니거나 미래인 값은 읽은 때를 지금으로 본다 — 창이 그때부터 300초다.
    # 지난 값(-1)과 값이 없는 것은 창 밖이다
    clock = Clock()
    if raw == "future":
        raw = str(int(clock.epoch()) + 3600)
    if raw is None:
        monkeypatch.delenv(follow.ENV_WORKER_RESTARTED_AT, raising=False)
    else:
        monkeypatch.setenv(follow.ENV_WORKER_RESTARTED_AT, raw)
    rw = rig.worker(now_fn=clock)
    rw.register()
    rig.bump(NEW)
    rw.heartbeat_once()
    assert rw.draining.is_set() is drains, rig.logs
    if not drains:
        assert rig.count(f"server runs v{NEW} — not draining: restarted 0s ago") == 1, rig.logs
        clock.advance(301)
        rw.heartbeat_once()
        assert rw.draining.is_set()


def test_flapping_versions_restart_at_most_once_per_window(rig, monkeypatch):
    # G9 · 결정 171: 30분 동안 heartbeat 마다 버전이 바뀌어도 재실행 간격은 언제나 300초 이상이고
    # 횟수는 7번 이하다. 재실행은 새 워커로 흉내 내고 그때 `RCM_WORKER_RESTARTED_AT` 을 그
    # 시각으로 둔다
    clock = Clock()
    start = clock.epoch()
    flip = {"n": 0}

    def forge(out):
        flip["n"] += 1
        return {**out, "server_version": NEW if flip["n"] % 2 else __version__}

    rig.forge = forge
    restarts: list[float] = []
    monkeypatch.delenv(follow.ENV_WORKER_RESTARTED_AT, raising=False)
    rw = rig.worker(now_fn=clock)
    rw.register()
    while clock.epoch() - start < 1800:
        clock.advance(10)
        rw.heartbeat_once()
        if rw.draining.is_set():  # 도는 잡이 없으니 드레인은 곧 재실행이다
            restarts.append(clock.epoch())
            monkeypatch.setenv(follow.ENV_WORKER_RESTARTED_AT, str(int(clock.epoch())))
            rw = rig.worker(now_fn=clock)
            rw.register()
    gaps = [b - a for a, b in zip(restarts, restarts[1:], strict=False)]
    assert restarts, rig.logs
    assert all(g >= follow.RESTART_WINDOW_SECONDS for g in gaps), gaps
    assert len(restarts) <= 7, restarts


# ── ④ 레인 재등록 409 ─────────────────────────────────────────────────────


def test_lane_reregister_409_drains_without_losing_the_other_lanes_job(rig):
    # B11 · W7 · R⑧ · E16: 한 레인만 409 `not registered` 를 받고 재등록이 버전 불일치 409 다.
    # `WorkerExit` 없이 드레인하고 다른 레인의 잡은 SIGTERM 없이 끝까지 가 보고된다. 워커 행이
    # 없으니 heartbeat 는 409 를 받고 「서버에 못 닿음」이 아니라 「등록 안 됨」이라고 쓴다
    rw = rig.start()
    jid = rig.hold()
    rig.forget()
    rig.bump(NEW)
    rig.until(rig.draining, "draining")
    lane_lines = [ln for ln in rig.logs if ln.endswith(f"server runs v{NEW} — {DRAINING}")]
    assert len(lane_lines) == 1 and lane_lines[0].startswith("lane "), rig.logs
    assert not rw.stopping.is_set()
    rig.until(lambda: rig.count("heartbeat: worker not registered") >= 1, "heartbeat 409 line")
    assert rig.count("server unreachable") == 0, rig.logs
    assert rig.count("install the same release") == 0, rig.logs
    rig.go(jid)
    assert rig.join() == 0, rig.logs
    job = rig.state(jid)
    assert job.state == SUCCEEDED, (job.state, job.summary)
    assert not rig.term(jid)
    assert rw.restart_for == NEW


def test_lane_409_drains_even_inside_the_window(rig, monkeypatch):
    # E9 · §3.3: ④ 는 300초 창과 상관없이 드레인한다 — 등록이 막힌 레인은 어차피 일을 못 한다
    monkeypatch.setenv(follow.ENV_WORKER_RESTARTED_AT, str(int(time.time()) - 10))
    rw = rig.start()
    rig.forget()
    rig.bump(NEW)
    assert rig.join() == 0, rig.logs
    assert rw.restart_for == NEW


def test_two_lanes_409_at_once_drain_once(rig):
    # G18: 두 레인이 함께 409 를 받아도 드레인과 로그는 한 번이다
    rw = rig.start()
    rig.until(lambda: len(rig.claims) >= 2, "both lanes polling")
    rig.forget()
    rig.bump(NEW)
    assert rig.join() == 0, rig.logs
    assert sum(1 for ln in rig.logs if DRAINING in ln) == 1, rig.logs
    assert rw.restart_for == NEW


def test_pinned_worker_lane_409_ends_saying_pinned(rig):
    # E14 · W12: 따라가지 않는 워커의 ④ 는 오늘처럼 끝난다 — 409 문장 뒤에 이유가 붙고 종료 2.
    # 다른 레인의 잡은 정지 경로로 lost 가 된다
    rw = rig.worker(follows=False, follow_tail="pinned by RCM_CLIENT_VERSION")
    rig.start()
    jid = rig.hold()
    rig.forget()
    rig.bump(NEW)
    assert rig.join() == 2, rig.logs
    tail = (
        f"worker version {__version__}, server {NEW} — install the same release"
        " · pinned by RCM_CLIENT_VERSION"
    )
    assert any(ln.startswith("lane ") and ln.endswith(tail) for ln in rig.logs), rig.logs
    assert not rw.draining.is_set()
    job = rig.state(jid)
    assert job.state == LOST and job.summary == remote_worker.STOP_SUMMARY
    assert rw.restart_for is None


# ── 따라가지 않는 워커 · --once ───────────────────────────────────────────


@pytest.mark.parametrize(
    "tail",
    [
        "not following (editable install)",
        "not following (RCM_CLIENT_VERSION=installed)",
        "pinned by RCM_CLIENT_VERSION",
    ],
)
def test_not_following_workers_ignore_the_heartbeat(rig, tail):
    # G10 · G11 · G12 · W11 · W17 · §14 B질문19: 드레인하지 않고 로그를 프로세스당 한 번 쓴다
    rw = rig.worker(follows=False, follow_tail=tail)
    rw.register()
    rig.bump(NEW)
    for _ in range(5):
        assert rw.heartbeat_once()
    assert not rw.draining.is_set()
    assert rig.logs == [f"server v{NEW} — {tail}"]


def test_unfollowing_worker_without_a_reason_is_silent(rig):
    # 결정 162: 인자 목록으로 부른 `main(argv)` 의 워커는 따라가지 않고 아무것도 쓰지 않는다
    rw = rig.worker(follows=False)
    rw.register()
    rig.bump(NEW)
    for _ in range(3):
        assert rw.heartbeat_once()
    assert not rw.draining.is_set()
    assert rig.logs == []


def test_once_ignores_the_heartbeat(rig):
    # G13 · W18 · §14 B질문4: `--once` 는 ③ 을 무시한다. 잡이 끝나면 끝나고 재실행은 없다
    rw = rig.worker(lanes=1, once=True)
    rig.start()
    jid = rig.hold()
    rig.bump(NEW)
    beats = len(rig.heartbeats)
    rig.until(lambda: len(rig.heartbeats) >= beats + 2, "heartbeats")
    assert not rw.draining.is_set()
    rig.go(jid)
    assert rig.join() == 0, rig.logs
    assert rig.state(jid).state == SUCCEEDED
    assert rw.restart_for is None


def test_once_lane_409_drains_then_ends_with_2(rig):
    # G14 · W18 · §14 B질문4: `--once` 의 ④ 는 드레인해 다른 레인의 잡을 보고한 뒤 재실행 없이
    # 종료 2 로 끝난다
    rw = rig.worker(lanes=2, once=True)
    rig.start()
    jid = rig.hold()
    rig.forget()
    rig.bump(NEW)
    rig.until(rig.draining, "draining")
    rig.go(jid)
    assert rig.join() == 2, rig.logs
    assert rig.state(jid).state == SUCCEEDED
    assert rw.restart_for is None


# ── 멈춤 · 보고 실패 ──────────────────────────────────────────────────────


def test_stop_during_the_drain_is_todays_stop(rig):
    # E12 · W13: 드레인 중의 정지는 오늘의 정지 경로다 — 도는 잡은 SIGTERM 을 받고 lost
    # `worker stopped` 이고 재실행은 없다
    rw = rig.start()
    jid = rig.hold()
    rig.bump(NEW)
    rig.until(rig.draining, "draining")
    rw.stop()
    assert rig.join() == 0, rig.logs
    rig.until(lambda: rig.term(jid), "job got SIGTERM")
    job = rig.state(jid)
    assert job.state == LOST and job.summary == remote_worker.STOP_SUMMARY
    assert rw.restart_for is None


def test_unreported_job_is_retried_before_the_restart(rig, monkeypatch):
    # E15 · §14 B질문11: finish 보고가 재시도 끝에도 실패하면 바로 재실행하지 않고 보고를 다시
    # 시도한다 — 재실행 뒤의 등록이 그 잡을 lost 로 닫지 않게
    monkeypatch.setattr(remote_worker, "REPORT_RETRIES", (0.05, 0.05, 0.05))
    rw = rig.start(lanes=1)
    jid = rig.hold()
    rig.bump(NEW)
    rig.until(rig.draining, "draining")
    rig.finish_fails = 4
    rig.go(jid)
    assert rig.join() == 0, rig.logs
    assert rig.count(f"drained — job #{jid} not reported yet, retrying before the restart") == 1
    assert rig.state(jid).state == SUCCEEDED
    assert rw.restart_for == NEW


def test_unreported_job_is_given_up_after_the_limit(rig, monkeypatch):
    # §14 B질문11 · §5.5: 상한(`DRAIN_REPORT_SECONDS`)이 지나면 그래도 재실행한다
    monkeypatch.setattr(remote_worker, "REPORT_RETRIES", (0.05, 0.05, 0.05))
    monkeypatch.setattr(remote_worker, "DRAIN_REPORT_SECONDS", 1.5)
    rw = rig.start(lanes=1)
    jid = rig.hold()
    rig.bump(NEW)
    rig.until(rig.draining, "draining")
    rig.finish_fails = 10_000
    rig.go(jid)
    t0 = time.monotonic()
    assert rig.join() == 0, rig.logs
    assert time.monotonic() - t0 < 15
    assert rig.count(f"job #{jid} still not reported — restarting anyway") == 1, rig.logs
    assert rw.restart_for == NEW


def test_the_drain_waits_for_the_report_constant():
    # §14 B질문11: 상수 이름과 값
    assert remote_worker.DRAIN_REPORT_SECONDS == 60


# ── 측정 프로브 정리 (§14 B질문13 · G22) ────────────────────────────────────


def test_release_probes_ends_a_running_probe(rig, tmp_path):
    # G22: exec 직전에 측정이 돌고 있으면 `grace_seconds` 만큼 기다린 뒤 그 프로브를 끝낸다 — exec
    # 뒤에 부모가 바뀐 프로브가 남지 않는다
    rw = rig.worker()
    pidfile = tmp_path / "probe.pid"
    script = f"echo $$ > {pidfile}; exec sleep 30"
    probe = Probe(name="slow", argv=("sh", "-c", script), timeout_seconds=60)
    done: dict[str, Any] = {}

    def measuring() -> None:
        with rw._measure_lock:
            done["r"] = hostsample.run_probes([probe], dict(os.environ))

    t = threading.Thread(target=measuring, daemon=True)
    t.start()
    rig.until(lambda: pidfile.exists() and pidfile.read_text().strip(), "probe started")
    pid = int(pidfile.read_text())
    t0 = time.monotonic()
    rw.release_probes()
    assert time.monotonic() - t0 < rw.config.grace_seconds + 3
    t.join(10)
    assert not t.is_alive()
    gone = subprocess.run(["ps", "-p", str(pid)], capture_output=True, text=True)
    assert gone.returncode != 0, gone.stdout


def test_release_probes_returns_at_once_when_nothing_measures(rig):
    rw = rig.worker()
    t0 = time.monotonic()
    rw.release_probes()
    assert time.monotonic() - t0 < 0.5


def test_heartbeat_409_line_keeps_the_failure_spacing(rig):
    # §14 B질문12: 3번째 · 12번째 · 그 뒤 60번째마다 — 연결 실패 줄과 같은 간격
    rw = rig.worker()
    for _ in range(12):
        assert rw.heartbeat_once() is False
    assert rig.logs == ["heartbeat: worker not registered"] * 2


# ── 보고하지 못한 잡 (#234 격리 검증) ───────────────────────────────────────


def test_a_job_finished_while_the_server_was_away_is_reported_before_the_drain(rig, monkeypatch):
    # 서버를 갈아 끼우는 몇 초에 잡이 끝나 보고 재시도가 다 실패해도 버리지 않는다. 서버가 새
    # 버전으로 돌아오면 다음 heartbeat 가 그 잡을 먼저 succeeded 로 보고하고, 그다음에 드레인 ·
    # 재실행한다
    monkeypatch.setattr(remote_worker, "REPORT_RETRIES", (0.05, 0.05, 0.05))
    rw = rig.start(lanes=1)
    jid = rig.hold()
    rig.down = True
    rig.go(jid)
    rig.until(lambda: sum(1 for _, j, st in rig.finishes if j == jid and st == 503) >= 4, "gave up")
    time.sleep(0.3)
    assert rig.state(jid).state != SUCCEEDED
    assert not rw.draining.is_set()
    rig.bump(NEW)
    rig.down = False
    assert rig.join() == 0, rig.logs
    job = rig.state(jid)
    assert job.state == SUCCEEDED and job.exit_code == 0, (job.state, job.summary)
    done = [at for at, j, st in rig.finishes if j == jid and st == 200]
    assert done and done[0] < rw.draining.set_at
    assert rw.restart_for == NEW
    # #234 재검증: 포기한다고 쓰지 않는다 — 미뤄 뒀다 다시 보고한다는 줄과 보고했다는 줄이 하나씩
    later = [ln for ln in rig.logs if ln.startswith(f"#{jid} finish: ")]
    assert len(later) == 2, rig.logs
    assert later[0].endswith(" — will retry after the server is back"), later
    assert later[1] == f"#{jid} finish: reported after retry"
    assert not any("giving up" in ln for ln in rig.logs), rig.logs


def test_registering_again_waits_for_a_retry_already_running(rig, monkeypatch):
    # #234 재검증: 다른 스레드(heartbeat)가 다시 보고하는 중이면 레인은 그 보고를 건너뛰고
    # 재등록하지 않는다 — 잠금을 잠깐 기다려 미룬 보고가 재등록보다 먼저 가게 한다
    monkeypatch.setattr(remote_worker, "REPORT_RETRIES", (0.05, 0.05, 0.05))
    rw = rig.start(lanes=2)
    jid = rig.hold()
    rig.down = True
    rig.go(jid)
    rig.until(lambda: jid in rw._unreported, "parked")
    rw._retry_lock.acquire()  # 다른 스레드가 다시 보고하는 중인 것처럼
    threading.Timer(1.5, rw._retry_lock.release).start()
    rig.forget()
    rig.down = False
    rig.until(lambda: rig.srv.store.get_worker(WORKER) is not None, "registered again")
    job = rig.state(jid)
    assert job.state == SUCCEEDED, (job.state, job.summary)
    rw.stop()
    assert rig.join() == 0


def test_a_job_not_reported_is_given_up_after_the_limit_without_a_drain(rig, monkeypatch):
    # 드레인이 아니어도 상한(`DRAIN_REPORT_SECONDS`)까지는 heartbeat 마다 다시 보고하고, 넘으면
    # 오늘처럼 로그를 쓰고 버린다 — heartbeat 목록에 영영 남지 않는다
    monkeypatch.setattr(remote_worker, "REPORT_RETRIES", (0.05, 0.05, 0.05))
    monkeypatch.setattr(remote_worker, "DRAIN_REPORT_SECONDS", 1.5)
    rw = rig.start(lanes=1)
    jid = rig.hold()
    rig.finish_fails = 10_000
    rig.go(jid)
    rig.until(lambda: rig.count(f"job #{jid} still not reported — giving up") == 1, "given up")
    rig.until(lambda: jid not in rw.running, "dropped from the heartbeat list")
    assert not rw.draining.is_set()


def test_a_job_not_reported_is_tried_again_before_registering_again(rig, monkeypatch):
    # 재등록은 그 워커의 도는 잡을 lost 로 닫는다. 서버가 워커를 잊어 레인이 다시 등록하기 전에
    # 보고하지 못한 잡을 먼저 보고한다
    monkeypatch.setattr(remote_worker, "REPORT_RETRIES", (0.05, 0.05, 0.05))
    rw = rig.start(lanes=2)
    jid = rig.hold()
    rig.down = True
    rig.go(jid)
    rig.until(lambda: jid in rw._unreported, "parked")
    rig.forget()
    rig.down = False  # heartbeat 는 이제 409(등록 안 됨)라 다시 보고할 기회는 재등록뿐이다
    rig.until(lambda: rig.srv.store.get_worker(WORKER) is not None, "registered again")
    job = rig.state(jid)
    assert job.state == SUCCEEDED, (job.state, job.summary)
    rw.stop()
    assert rig.join() == 0


def test_no_probe_starts_after_the_probes_were_ended(monkeypatch, tmp_path):
    # #234 격리 검증: 프로브를 끝낸 뒤(곧 exec 한다)에는 새 프로브를 띄우지 않는다 — 시작과 등록
    # 사이에 끼인 프로브가 exec 뒤에 남지 않게
    monkeypatch.setattr(hostsample, "_PROBES_CLOSED", False)
    hostsample.kill_probes()
    mark = tmp_path / "ran"
    probe = Probe(name="late", argv=("sh", "-c", f"touch {mark}"), timeout_seconds=5)
    out = hostsample.run_probes([probe], dict(os.environ))
    assert "late" in out
    assert not mark.exists()
