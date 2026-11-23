"""M5s D — 종료코드 반복의 e2e: 상한까지 돌고 `exhausted`(S5), 사슬 취소(S13), 75 직전의 취소(S20),
첫 id 로 `rcm cancel`(S26), 그룹에 막힌 반복 시도(S27) (`docs/m5s-workplan.md` §4.2 · §7).

진짜 `App.start()`(로컬 레인 · 알림 스레드 · 5초 주기 루프)와 HTTP 를 쓴다. 시계만 주입한다 —
반복 시도의 승격은 `tick_schedule()` 을 시험이 불러 앞당긴다. 반복 프리셋은 git_ref 만 받으므로
(결정 211) 워커의 자재화(`git fetch`)는 빈 워크스페이스로 바꿔 끼운다 — 이 파일의 주제는 종료코드다.
알림 훅은 받은 상태를 파일에 한 줄씩 적는 가짜다.
"""

from __future__ import annotations

import http.client
import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from remote_ci_monitor import cli
from remote_ci_monitor.config import load_server_config
from remote_ci_monitor.core.model import (
    CANCELLED,
    EXHAUSTED,
    QUEUED,
    RETRIED,
    RUNNING,
    SCHEDULED,
    SUCCEEDED,
)
from remote_ci_monitor.server import App, make_server
from remote_ci_monitor.store import Store
from remote_ci_monitor.worker import Worker
from test_worker_api import GIT_SHA, Clock

CONFIG = """
[server]
bind = "127.0.0.1"
data_dir = "{data}"
lanes = 2
grace_seconds = 1
admission = "always"
advertise = false

[display]
timezone = "UTC"

[[notify]]
name = "file-hook"
argv = ["sh", "-c", "echo $RCM_JOB_ID:$RCM_STATE >> {log}"]
timeout_seconds = 10

[[repos]]
name = "app"
url = "{data}/nowhere.git"

[[presets]]
name = "later"
argv = ["sh", "-c", "echo '::rcm::summary::not yet'; exit 75"]
source_modes = ["git_ref"]
repo = "app"
concurrency_group = "chrome"
[presets.retry]
on_exit = 75
interval_seconds = 60
max_attempts = 3

[[presets]]
name = "hold"
argv = ["sh", "-c", "trap 'exit 75' TERM; i=0; while [ $i -lt 300 ]; do sleep 0.1; \
i=$((i+1)); done; exit 75"]
source_modes = ["git_ref"]
repo = "app"
[presets.retry]
interval_seconds = 60

[[presets]]
name = "login"
argv = ["sh", "-c", "i=0; while [ ! -f {release} ] && [ $i -lt 300 ]; do sleep 0.1; \
i=$((i+1)); done"]
source_modes = ["git_ref"]
repo = "app"
concurrency_group = "chrome"
timeout_seconds = 86400
"""


class Live:
    def __init__(self, tmp: Path, clock: Clock):
        self.data = tmp / "data"
        self.log = tmp / "notify.log"
        self.release = tmp / "release"
        self.path = tmp / "rcm.toml"
        self.path.write_text(
            CONFIG.format(data=self.data, log=self.log, release=self.release), encoding="utf-8"
        )
        cfg = load_server_config(self.path)
        self.store = Store(cfg.data_dir / "rcm.sqlite3")
        self.app = App(cfg, self.store, now_fn=clock)
        self.clock = clock
        self.token = self.store.add_token("alice-laptop", admin=False, now=clock())
        self.httpd = make_server(self.app, bind="127.0.0.1", port=0)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self.thread.start()
        self.app.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def down(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.app.shutdown()
        self.store.close()

    def req(self, method: str, path: str, body: Any = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        headers = {"Authorization": f"Bearer {self.token}"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(data))
        conn.request(method, path, body=data, headers=headers)
        resp = conn.getresponse()
        payload = resp.read()
        conn.close()
        return resp.status, (json.loads(payload) if payload else None)

    def git(self, preset: str, **extra: Any) -> dict[str, Any]:
        body = {
            "preset": preset,
            "inputs": {},
            "source": {"mode": "git_ref", "ref": GIT_SHA},
            "join": False,
            **extra,
        }
        status, resp = self.req("POST", "/jobs", body)
        assert status == 201, resp
        return resp

    def job(self, jid: int):
        return self.store.get_job(jid)

    def wait_state(self, jid: int, want: set[str], timeout: float = 15.0) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            got = self.job(jid).state
            if got in want:
                return got
            time.sleep(0.05)
        raise AssertionError(f"job {jid} stayed {self.job(jid).state}, wanted {want}")

    def next_of(self, jid: int) -> int:
        self.wait_state(jid, {RETRIED})
        nxt = self.job(jid).retried_as
        assert nxt is not None
        assert self.job(nxt).state == SCHEDULED
        return nxt

    def promote(self, seconds: float = 61) -> None:
        self.clock.advance(seconds)
        self.app.tick_schedule()

    def notified(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []

    def wait_notified(self, want: str, timeout: float = 10.0) -> list[str]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if want in self.notified():
                return self.notified()
            time.sleep(0.05)
        raise AssertionError(f"notify hook never saw {want}: {self.notified()}")


@pytest.fixture
def live(tmp_path, monkeypatch):
    # git_ref 의 자재화(fetch)는 이 파일의 주제가 아니다 — 빈 워크스페이스로 바꿔 끼운다
    monkeypatch.setattr(
        Worker, "_materialize", lambda self, job, preset, ws, log: ws.mkdir(parents=True)
    )
    lv = Live(tmp_path, Clock())
    yield lv
    lv.release.write_text("go")
    lv.down()


def test_s5_a_75_preset_retries_at_its_interval_until_exhausted(live):
    root = live.git("later")["job_id"]
    second = live.next_of(root)
    assert live.job(root).summary == "not yet"  # 잡이 찍은 문장이 이긴다
    live.app.tick_schedule()
    assert live.job(second).state == SCHEDULED  # 간격 전에는 오르지 않는다
    live.promote()
    third = live.next_of(second)
    live.promote()
    assert live.wait_state(third, {EXHAUSTED}) == EXHAUSTED
    attempts = [live.job(j) for j in (root, second, third)]
    assert [a.attempt for a in attempts] == [1, 2, 3]
    assert {a.chain_id for a in attempts} == {root}
    assert [a.exit_code for a in attempts] == [75, 75, 75]
    lines = live.wait_notified(f"{third}:{EXHAUSTED}")
    # 시도마다 울리지 않는다 — `retried` 는 기본 `on` 밖이다(결정 211)
    assert not [line for line in lines if line.endswith(f":{RETRIED}")]
    # 최근 목록은 사슬의 마지막 시도 한 행이다
    _, doc = live.req("GET", "/api/status")
    recent = [r["id"] for p in doc["pools"] for r in p["recent"]]
    assert third in recent and root not in recent and second not in recent


def test_s13_cancelling_the_chain_also_cancels_the_waiting_next_attempt(live):
    root = live.git("later")["job_id"]
    second = live.next_of(root)
    status, body = live.req("POST", f"/jobs/{root}/cancel", {})
    assert status == 200, body
    assert body["job_id"] == second and live.job(second).state == CANCELLED
    live.promote(3600)
    assert live.job(second).state == CANCELLED
    assert live.store.list_scheduled() == []
    assert [j.id for j in live.store.chain_jobs(root)] == [root, second]


def test_s20_a_cancel_that_makes_the_script_exit_75_closes_cancelled_with_no_retry(live):
    """취소의 SIGTERM 에 스크립트가 75 로 끝나도(로그인 대기 중의 흔한 모양) 반복하지 않는다."""
    root = live.git("hold")["job_id"]
    live.wait_state(root, {RUNNING})
    status, _ = live.req("POST", f"/jobs/{root}/cancel", {})
    assert status == 200
    # 실행기는 주입 시계로 1초마다 취소를 본다 — 시계를 민다
    deadline = time.monotonic() + 15
    while not live.job(root).is_terminal and time.monotonic() < deadline:
        live.clock.advance(2)
        time.sleep(0.05)
    assert live.wait_state(root, {CANCELLED, RETRIED}) == CANCELLED
    assert live.job(root).exit_code in (75, None)
    assert live.store.list_scheduled() == []


def test_s26_rcm_cancel_with_the_first_id_stops_the_live_head(live, monkeypatch, tmp_path, capsys):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("XDG_CONFIG_HOME", "RCM_LABEL", "RCM_CONFIG"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("RCM_SERVER", live.url)
    monkeypatch.setenv("RCM_TOKEN", live.token)
    root = live.git("later")["job_id"]
    second = live.next_of(root)
    code = cli.main(["cancel", str(root)])
    out = capsys.readouterr()
    assert code == 0, out.err
    assert json.loads(out.out.strip().splitlines()[-1])["job_id"] == second
    assert live.job(second).state == CANCELLED


def test_s27_a_retry_attempt_held_by_its_group_waits_without_expiring(live):
    """결정 231 — 사람이 로그인하는 동안(로그인 프리셋이 같은 그룹을 쥐고 있다) 다음 시도는
    `blocked_by_group` 으로 기다린다. 시작 마감이 없으므로 몇 시간이 지나도 `expired` 가 아니다."""
    root = live.git("later")["job_id"]
    second = live.next_of(root)
    login = live.git("login")["job_id"]
    live.wait_state(login, {RUNNING})
    live.promote()
    assert live.job(second).state == QUEUED
    for _ in range(4):
        live.promote(3600)  # 4시간
        assert live.job(second).state == QUEUED
    _, doc = live.req("GET", "/api/status")
    row = next(r for p in doc["pools"] for r in p["queue"] if r["id"] == second)
    assert row["reason"] == "blocked_by_group" and row["attempt"] == 2
    live.release.write_text("go")
    assert live.wait_state(login, {SUCCEEDED}) == SUCCEEDED
    assert live.wait_state(second, {RETRIED}) == RETRIED  # 그룹이 풀리면 돈다
