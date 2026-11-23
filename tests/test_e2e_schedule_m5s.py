"""M5s A — 예약의 e2e: reload · 재시작에도 남는다(S3), 놓치면 `expired` 이고 알림이 울린다(S4)
(`docs/m5s-workplan.md` §4.1 · §4.7 · §7).

진짜 `App.start()`(로컬 레인 · 알림 스레드 · 5초 주기 루프)와 HTTP 를 쓴다. 시계만 주입한다 —
승격과 만료는 `tick_schedule()` 을 시험이 불러 앞당기고, 재시작 정리는 `start()` 가 한다.
알림 훅은 받은 상태를 파일에 한 줄씩 적는 가짜다(`[[notify]]` argv).
"""

from __future__ import annotations

import http.client
import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from remote_ci_monitor.config import load_server_config
from remote_ci_monitor.core.model import EXPIRED, QUEUED, RUNNING, SCHEDULED, SUCCEEDED
from remote_ci_monitor.server import App, make_server
from remote_ci_monitor.store import Store
from test_server import TAR
from test_worker_api import GIT_SHA, Clock

CONFIG = """
[server]
bind = "127.0.0.1"
data_dir = "{data}"
lanes = 1
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
name = "ok"
argv = ["sh", "-c", "cat hello.txt"]
timeout_seconds = 60

[[presets]]
name = "block"
argv = ["sh", "-c", "i=0; while [ ! -f {release} ] && [ $i -lt 300 ]; do sleep 0.1; i=$((i+1)); \
done"]
timeout_seconds = 7200  # 시험이 시계를 1000초 민다 — 그 사이 시간 초과로 닫히면 안 된다

[[presets]]
name = "quick"
argv = ["true"]
source_modes = ["git_ref"]
repo = "app"
start_deadline_seconds = 60
{extra}
"""


class Live:
    """설정 파일 · 주입 시계 · HTTP 위의 App 하나. `restart()` 는 같은 data_dir 로 다시 띄운다."""

    def __init__(self, tmp: Path, clock: Clock):
        self.tmp = tmp
        self.clock = clock
        self.data = tmp / "data"
        self.log = tmp / "notify.log"
        self.release = tmp / "release"
        self.path = tmp / "rcm.toml"
        self.write()
        self.app: App | None = None
        self.token = ""
        self._up()
        self.token = self.app.store.add_token("alice-laptop", admin=True, now=clock())

    def write(self, extra: str = "") -> None:
        self.path.write_text(
            CONFIG.format(data=self.data, log=self.log, release=self.release, extra=extra),
            encoding="utf-8",
        )

    def _up(self) -> None:
        cfg = load_server_config(self.path)
        store = Store(cfg.data_dir / "rcm.sqlite3")
        self.app = App(cfg, store, now_fn=self.clock)
        self.httpd = make_server(self.app, bind="127.0.0.1", port=0)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self.thread.start()
        self.app.start()

    def down(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        assert self.app is not None
        self.app.shutdown()
        self.app.store.close()

    def restart(self) -> None:
        self.down()
        self._up()

    def req(self, method: str, path: str, body: Any = None, raw: bytes | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        headers = {"Authorization": f"Bearer {self.token}"}
        data = raw
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if data is not None:
            headers["Content-Length"] = str(len(data))
        conn.request(method, path, body=data, headers=headers)
        resp = conn.getresponse()
        payload = resp.read()
        conn.close()
        return resp.status, (json.loads(payload) if payload else None)

    def tree(self, preset: str, tree_hash: str, **extra: Any) -> int:
        status, resp = self.req(
            "POST",
            "/jobs",
            {
                "preset": preset,
                "inputs": {},
                "source": {"mode": "tree", "tree_hash": tree_hash, "bytes": len(TAR)},
                **extra,
            },
        )
        assert status == 201, resp
        jid = int(resp["job_id"])
        status, up = self.req("PUT", f"/jobs/{jid}/tree", raw=TAR)
        assert status == 200, up
        return jid

    def git(self, preset: str, *, ref: str = GIT_SHA, **extra: Any) -> int:
        body = {
            "preset": preset,
            "inputs": {},
            "source": {"mode": "git_ref", "ref": ref},
            **extra,
        }
        status, resp = self.req("POST", "/jobs", body)
        assert status == 201, resp
        return int(resp["job_id"])

    def state(self, jid: int) -> str:
        assert self.app is not None
        return self.app.store.get_job(jid).state

    def wait_state(self, jid: int, want: set[str], timeout: float = 15.0) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            got = self.state(jid)
            if got in want:
                return got
            time.sleep(0.05)
        raise AssertionError(f"job {jid} stayed {self.state(jid)}, wanted {want}")

    def notified(self, timeout: float = 10.0, *, want: str) -> list[str]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            lines = self.log.read_text().splitlines() if self.log.exists() else []
            if want in lines:
                return lines
            time.sleep(0.05)
        raise AssertionError(f"notify hook never saw {want}: {lines}")


@pytest.fixture
def live(tmp_path):
    lv = Live(tmp_path, Clock())
    yield lv
    lv.release.write_text("go")
    lv.down()


def test_s3_schedules_survive_a_reload_and_a_restart_s4_down_past_deadline_expires(live):
    due = live.tree("ok", "a" * 64, start_in_seconds=600)
    late = live.git("quick", start_in_seconds=60)
    future = live.git("quick", ref="f" * 40, start_in_seconds=7200)
    assert {live.state(j) for j in (due, late, future)} == {SCHEDULED}

    live.write(extra='\n[[presets]]\nname = "more"\nargv = ["true"]\n')
    assert live.app.reload_config()["presets_added"] == ["more"]
    assert {live.state(j) for j in (due, late, future)} == {SCHEDULED}  # S3 reload

    live.clock.advance(700)  # 서버가 꺼진 동안 late 의 마감(120초)이 지났다
    live.restart()
    assert live.state(late) == EXPIRED
    assert live.state(future) == SCHEDULED
    assert live.wait_state(due, {QUEUED, RUNNING, SUCCEEDED}) in {QUEUED, RUNNING, SUCCEEDED}
    assert live.wait_state(due, {SUCCEEDED}) == SUCCEEDED
    assert f"{late}:{EXPIRED}" in live.notified(want=f"{late}:{EXPIRED}")


def test_s4_a_lane_that_stays_busy_past_the_deadline_expires_the_schedule(live):
    blocker = live.tree("block", "b" * 64)
    live.wait_state(blocker, {RUNNING})
    sched = live.tree("ok", "c" * 64, start_in_seconds=60)
    assert live.state(sched) == SCHEDULED
    live.clock.advance(60)
    live.app.tick_schedule()
    assert live.state(sched) == QUEUED  # 승격됐지만 레인이 바쁘다
    live.clock.advance(901)
    live.app.tick_schedule()
    assert live.state(sched) == EXPIRED
    lines = live.notified(want=f"{sched}:{EXPIRED}")
    assert f"{sched}:{EXPIRED}" in lines
    live.release.write_text("go")
    assert live.wait_state(blocker, {SUCCEEDED}) == SUCCEEDED
    assert live.state(sched) == EXPIRED  # 레인이 비어도 닫힌 예약은 돌지 않는다
