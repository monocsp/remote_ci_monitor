"""e2e(M5n · 역할 B) — 진짜 `rcm serve` + 진짜 `rcm worker` 둘, 같은 머신의 세 프로세스.

명세는 docs/m5n-workplan.md §16 완료 기준 1·2 와 §14.1 edge 1 · 5 · 8. 시나리오 표는
docs/m5n-test-scenarios-b.md. 구현 전에는 `rcm serve` 가 설정의 `[[probes]]` 를 못 읽고 종료
코드 2 로 죽어서 픽스처가 서버 로그째로 빨갛다.

배치:
- 서버: `rcm serve --config server.toml`. 임시 data dir · 로컬 레인 1 · heartbeat 1초 ·
  `check_sweep_seconds = 1`(가정 B-11: 하한이 1 이하) · `probe_interval_seconds = 60`(하한).
  서버 PATH 에는 가짜 도구가 **없다** — 로컬은 `tool_missing` 이다.
- 워커 `w-cap`(풀 capp): PATH 맨 앞에 `fakebin/` — `rcm-m5n-faketool` 과 `rcm-m5n-fakexc`
  (`xc-version.txt` 를 그대로 찍는 가짜 `xcodebuild -version`)가 있다.
- 워커 `w-nocap`(풀 nocapp): 같은 PATH 에서 `fakebin/` 만 뺐다.
- 소스는 전부 `git_ref`(tmp bare 레포 · `gitrepo.build_remote`) — tree 업로드 없이 제출한다.
- 잡은 짧게(`sleep 0.3`) — 폴링이 도는 순간을 본다. 모든 기다림은 마감 있는 폴링이다.
- 도우미(`free_port` · `rcm` · `clean_env` · `kill_group` · `wait_until` · `E2EServer`)는
  `tests/test_e2e_worker.py` 의 것을 그대로 쓴다.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from gitrepo import build_remote
from remote_ci_monitor.store import Store
from test_e2e_worker import (
    RCM,
    TERMINAL,
    E2EServer,
    clean_env,
    free_port,
    kill_group,
    wait_until,
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")

FAKE_TOOL = "rcm-m5n-faketool"
FAKE_XC = "rcm-m5n-fakexc"
CAP, NOCAP = "w-cap", "w-nocap"
POOLS = {CAP: "capp", NOCAP: "nocapp"}
LOCAL = "@local"

SERVER_TOML = """\
[server]
bind = "127.0.0.1"
port = {port}
data_dir = "{data_dir}"
lanes = 1
grace_seconds = 2
admission = "always"
worker_timeout_seconds = 10
worker_heartbeat_seconds = 1
worker_claim_wait_seconds = 2
probe_interval_seconds = 60
check_sweep_seconds = 1

[[repos]]
name = "app"
url = "{url}"

[[probes]]
name = "xc"
argv = ["{fake_xc}"]
timeout_seconds = {probe_timeout}

[[presets]]
name = "capgate"
placement = "any-capable"
requires = ["{fake_tool}"]
source_modes = ["git_ref"]
repo = "app"
argv = ["sh", "{scripts}/capgate.sh"]
timeout_seconds = 60

[[presets]]
name = "simcheck"
source_modes = ["git_ref"]
repo = "app"
argv = ["sh", "{scripts}/simcheck.sh"]
timeout_seconds = 60

[[presets]]
name = "scenario"
placement = "any-capable"
requires = ["{fake_xc}"]
checks = ["simcheck"]
source_modes = ["git_ref"]
repo = "app"
argv = ["sh", "-c", "{fake_xc}; sleep 0.3"]
timeout_seconds = 60
"""

WORKER_TOML = """\
[[repos]]
name = "app"
url = "{url}"
"""


# ── 프로세스 ─────────────────────────────────────────────────────────────────


class M5nE2EServer(E2EServer):
    """`E2EServer` 의 수명 · HTTP 도우미는 그대로, 설정만 이 파일의 것."""

    def __init__(self, tmp_path: Path, *, url: str, scripts: Path):
        self.port = free_port()
        self.data_dir = tmp_path / "server-data"
        self.config = tmp_path / "server.toml"
        self.log = tmp_path / "server.log"
        home = tmp_path / "home"
        home.mkdir(exist_ok=True)
        self.env = clean_env(home)
        self._url = url
        self._scripts = scripts
        self.write_config(probe_timeout=5)
        self.proc = None
        self._out = None

    def write_config(self, *, probe_timeout: int) -> None:
        self.config.write_text(
            SERVER_TOML.format(
                port=self.port,
                data_dir=self.data_dir,
                url=self._url,
                fake_xc=FAKE_XC,
                fake_tool=FAKE_TOOL,
                scripts=self._scripts,
                probe_timeout=probe_timeout,
            )
        )

    def post(self, path: str, token: str, body: dict[str, Any]) -> tuple[int, Any]:
        req = urllib.request.Request(
            self.url + path,
            data=json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return resp.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raw = e.read()
            return e.code, json.loads(raw) if raw else {}


class M5nWorker:
    """`rcm worker` 하나. 토큰은 `RCM_WORKER_TOKEN`, PATH 는 워커마다 다르다."""

    def __init__(self, server: M5nE2EServer, name: str, token: str, tmp: Path, path: str):
        self.server = server
        self.name = name
        self.log = tmp / f"{name}.log"
        self.argv = [
            *RCM,
            "worker",
            "--server",
            server.url,
            "--pool",
            POOLS[name],
            "--lanes",
            "1",
            "--data",
            str(tmp / f"{name}-data"),
            "--config",
            str(tmp / "worker.toml"),
        ]
        self.env = {**server.env, "RCM_WORKER_TOKEN": token, "PATH": path}
        self.proc: subprocess.Popen | None = None
        self._out = None

    def start(self) -> None:
        self._out = self.log.open("ab")
        self.proc = subprocess.Popen(
            self.argv,
            env=self.env,
            stdout=self._out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    def log_text(self) -> str:
        return self.log.read_text(errors="replace") if self.log.exists() else ""

    def kill(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            kill_group(self.proc.pid)
            self.proc.wait(timeout=10)
        if self._out is not None:
            self._out.close()
            self._out = None


class Stack:
    """서버 + 워커 둘 + 클라이언트·admin 토큰. 상태는 HTTP 로 읽는다."""

    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        remote_root = tmp_path / "remote"
        remote_root.mkdir()
        self.remote = build_remote(remote_root)
        self.state = tmp_path / "state"  # 가짜 도구가 읽는 파일들
        self.state.mkdir()
        self.set_xc("Xc 1")
        self.fakebin = tmp_path / "fakebin"
        self.fakebin.mkdir()
        self._tool(FAKE_TOOL, "echo faketool ok\n")
        self._tool(FAKE_XC, f'cat "{self.state}/xc-version.txt"\n')
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        (scripts / "capgate.sh").write_text(f"{FAKE_TOOL} && sleep 0.3\n")
        # 고쳐졌다는 표시(fixed.flag)가 있거나 xc 가 「Xc 1」 일 때만 통과하는 점검
        (scripts / "simcheck.sh").write_text(
            f'if [ -f "{self.state}/fixed.flag" ]; then sleep 0.3; exit 0; fi\n'
            f'v="$({FAKE_XC})"\nsleep 0.3\n[ "$v" = "Xc 1" ]\n'
        )
        (tmp_path / "worker.toml").write_text(WORKER_TOML.format(url=self.remote.url))
        self.server = M5nE2EServer(tmp_path, url=self.remote.url, scripts=scripts)
        self.client = self.server.token("laptop")
        self.admin = self.server.token("ops", admin=True)
        base_path = self.server.env["PATH"]
        assert shutil.which(FAKE_TOOL, path=base_path) is None
        self.paths = {CAP: f"{self.fakebin}{os.pathsep}{base_path}", NOCAP: base_path}
        self.workers: dict[str, M5nWorker] = {}

    def _tool(self, name: str, body: str) -> None:
        p = self.fakebin / name
        p.write_text("#!/bin/sh\n" + body)
        p.chmod(0o755)

    def set_xc(self, value: str) -> None:
        (self.state / "xc-version.txt").write_text(value + "\n")

    def start(self, *names: str) -> None:
        self.server.start()
        for name in names:
            token = self.server.token(name, worker=True)
            w = M5nWorker(self.server, name, token, self.tmp, self.paths[name])
            self.workers[name] = w
            w.start()
        for name in names:
            self.wait_reported(name)

    def wait_reported(self, name: str, timeout: float = 30) -> None:
        """`/api/machines` 에 그 워커가 지문과 함께 보일 때까지. 먼저 죽으면 로그째."""
        w = self.workers[name]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            assert w.proc is not None
            if w.proc.poll() is not None:
                raise AssertionError(f"{name} exited {w.proc.returncode}:\n{w.log_text()}")
            m = self.machines().get(name)
            if m and m["alive"] and m["fingerprint"]["hash"]:
                return
            time.sleep(0.1)
        raise AssertionError(f"{name} never reported:\n{w.log_text()}\n{self.server.log_text()}")

    def machines(self) -> dict[str, dict[str, Any]]:
        try:
            status, doc = self.server.get("/api/machines", self.client)
        except OSError:
            return {}
        if status != 200:
            return {}
        return {m["id"]: m for m in doc["machines"]}

    def submit(self, preset: str) -> int:
        body = {
            "preset": preset,
            "inputs": {},
            "source": {"mode": "git_ref", "ref": "main"},
            "requester_label": "laptop@e2e",
            "join": False,
        }
        status, resp = self.server.post("/jobs", self.client, body)
        assert status == 201, resp
        return int(resp["job_id"])

    def job(self, jid: int) -> dict[str, Any]:
        status, doc = self.server.get(f"/jobs/{jid}", self.client)
        assert status == 200, doc
        return doc

    def wait_terminal(self, jid: int, timeout: float = 40) -> dict[str, Any]:
        last: dict[str, Any] = {}

        def done() -> bool:
            nonlocal last
            last = self.job(jid)
            return last["state"] in TERMINAL

        try:
            wait_until(done, timeout, f"job #{jid} to finish")
        except AssertionError:
            logs = "\n".join(w.log_text() for w in self.workers.values())
            raise AssertionError(f"job #{jid} is {last.get('state')}:\n{logs}") from None
        return last

    def wait_check(self, worker: str, state: str, *, other_than: int | None = None) -> dict:
        """그 워커의 simcheck 점검이 `state` 가 될 때까지(`other_than` 이 아닌 잡으로)."""
        found: dict[str, Any] = {}

        def ready() -> bool:
            nonlocal found
            m = self.machines().get(worker)
            checks = {c["preset"]: c for c in (m or {}).get("checks", [])}
            found = checks.get("simcheck", {})
            return found.get("state") == state and found.get("job_id") not in (None, other_than)

        wait_until(ready, 40, f"{worker} simcheck {state}")
        return found

    def store_jobs(self, preset: str) -> list[Any]:
        store = Store(self.server.data_dir / "rcm.sqlite3")
        try:
            return [j for j in store.list_jobs_by_preset([preset], 100)]
        finally:
            store.close()

    def teardown(self) -> None:
        for w in self.workers.values():
            w.kill()
        self.server.kill()


@pytest.fixture
def stack(tmp_path: Path):
    st = Stack(tmp_path)
    yield st
    st.teardown()


# ── 1. any-capable 잡은 도구가 있는 워커로만 (edge 5 · 8 · 완료 기준 1) ─────────


def test_an_any_capable_job_runs_only_on_the_worker_that_has_the_tool(stack):
    stack.start(CAP, NOCAP)
    machines = stack.machines()
    tool_missing = {"state": "ineligible", "code": "tool_missing", "detail": {"tools": [FAKE_TOOL]}}
    assert machines[NOCAP]["presets"]["capgate"] == tool_missing
    assert machines[LOCAL]["presets"]["capgate"] == tool_missing  # 서버 PATH 에도 없다
    assert machines[CAP]["presets"]["capgate"] == {"state": "eligible", "code": "ok", "detail": {}}
    # 같은 Mac 의 세 워커 — 호스트 하나로 묶인다(하드웨어 id 를 못 읽는 곳은 워커별 키)
    keys = {machines[w]["host_key"] for w in (CAP, NOCAP, LOCAL)}
    assert len(keys) == 1 or all(k.startswith("worker:") for k in keys), keys

    ids = [stack.submit("capgate") for _ in range(3)]
    for jid in ids:
        assert stack.wait_terminal(jid)["state"] == "succeeded"
    for job in stack.store_jobs("capgate"):
        assert job.worker_name == CAP, job
        assert (job.pool, job.display_pool, job.placement) == ("capp", "default", "capable")
        assert job.host_key == machines[CAP]["host_key"]  # 끝난 표본은 그 호스트로(edge 8)
    assert sorted(j.id for j in stack.store_jobs("capgate")) == ids


# ── 2. Xcode 사고의 재연 (edge 1 · 완료 기준 2) ───────────────────────────────


@pytest.mark.slow
def test_a_changed_probe_triggers_one_automatic_check_and_a_failure_sticks(stack):
    stack.start(CAP)
    # 등록 직후: 지문 → scenario 는 check_needed → 스위퍼가 w-cap 을 지정해 simcheck 를 한 번
    first = stack.wait_check(CAP, "passed")
    view = stack.job(first["job_id"])
    assert view["requester"]["name"] == "rcm:check"
    assert first["auto_submitted"] is True
    m = stack.machines()[CAP]
    assert m["presets"]["scenario"] == {"state": "eligible", "code": "ok", "detail": {}}
    assert m["fingerprint"]["probes"]["xc"]["value"] == "Xc 1"
    assert m["fingerprint"]["changed_at"] is None

    # 머신의 환경이 바뀐다 — 그리고 설정을 다시 읽어 세대를 올린다(워커가 명세를 다시 받아 잰다)
    stack.set_xc("Xc 2")
    stack.server.write_config(probe_timeout=6)
    status, body = stack.server.post("/reload", stack.admin, {})
    assert status == 200, body
    wait_until(
        lambda: stack.machines()[CAP]["fingerprint"]["probes"]["xc"]["value"] == "Xc 2",
        30,
        "w-cap to re-measure after the reload",
    )
    second = stack.wait_check(CAP, "failed", other_than=first["job_id"])
    m = stack.machines()[CAP]
    assert (m["presets"]["scenario"]["state"], m["presets"]["scenario"]["code"]) == (
        "ineligible",
        "check_failed",
    )
    assert m["fingerprint"]["probes"]["xc"]["previous"] == "Xc 1"
    assert m["fingerprint"]["changed_at"] is not None
    assert second["auto_submitted"] is True

    # 실패는 덮이지 않는다 — 스위퍼가 몇 바퀴 돌아도 같은 지문에 다시 안 건다
    time.sleep(3.5)
    assert sorted(j.id for j in stack.store_jobs("simcheck")) == [first["job_id"], second["job_id"]]

    # 사람이 고치고 「다시 점검」 → 초록으로 돌아온다
    (stack.state / "fixed.flag").write_text("idb fixed\n")
    status, body = stack.server.post(f"/machines/{CAP}/recheck", stack.admin, {})
    assert status == 200, body
    [again] = [int(j["job_id"]) for j in body["jobs"]]
    assert stack.wait_terminal(again)["state"] == "succeeded"
    wait_until(
        lambda: stack.machines()[CAP]["presets"]["scenario"]["code"] == "ok",
        10,
        "scenario to be eligible again",
    )
