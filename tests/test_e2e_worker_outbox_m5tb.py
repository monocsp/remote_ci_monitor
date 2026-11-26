"""e2e: 원격 잡의 보고 큐가 분단 · kill -9 · exec 를 넘어 살아남는다(M5t-b §4.4 · §4.5).

RL6 b · RL16 · RL17 a · RL14 a · RL18 a~c 는 진짜 서버 프로세스 + 진짜 `rcm worker` + 표준
라이브러리 TCP 중계(`tests/tcp_relay.py`)다. 하네스는 `tests/test_e2e_worker_grace_m5tb.py` 의
`Grace` 를 쓰고 프리셋을 더한다. 출력이 정해진 잡이라 서버의 `log.txt` 를 그 출력과 바이트로
비교한다(가릴 값이 없으면 sha256 일치, 릴리스 비밀이 있으면 `****` 로 바꾼 출력과 일치).

RL12 b 는 `tests/test_e2e_worker_m5q.py` 의 하네스다. 비 editable 설치본 워커가 서버 버전이
바뀐 것을 보고 드레인한 뒤 같은 PID 로 진짜 exec 한다. 서버는 pytest 안의 `WorkerServer` 라
finish 에 503 을 줄 수 있다.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import signal
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from follow_helpers import INSTALLED, NEW, make_bootloader
from remote_ci_monitor.config import parse_release_profile
from remote_ci_monitor.core.model import SUCCEEDED
from remote_ci_monitor.release_secrets import SecretStore, secrets_dir
from remote_ci_monitor.server import ApiError
from test_e2e_worker import RCM, rcm
from test_e2e_worker_grace_m5tb import Grace, GWorker
from test_e2e_worker_m5q import DRAINING, Stack, Supervisor, add_presets, wait_for

LINES = 4000
#: 한 줄씩 천천히 내는 잡 — 분단 동안에도 출력이 쌓인다. 마지막 줄에는 줄 끝이 없다(RL17 a)
COUNT = (
    "i=0; while [ $i -lt " + str(LINES) + " ]; do echo line-$i; i=$((i+1));"
    " if [ $((i % 400)) -eq 0 ]; then sleep 1; fi; done; printf tail-without-newline"
)
EXTRA = f"""
[[presets]]
name = "count"
pool = "linux"
argv = ["sh", "-c", '{COUNT}']
timeout_seconds = 300
"""
#: 한 줄을 낸 뒤 시험이 `go.<잡>` 을 만들 때까지 기다렸다가 5 로 끝난다(RL14 a). 5 는 lost 의
#: 종료 3 과 다른 「진짜 결과」다
GATED = (
    'echo before; while [ ! -e "$RCM_MARK_DIR/go.$RCM_JOB_ID" ]; do sleep 0.1; done;'
    " echo after; exit 5"
)
GATED_TOML = """
[[presets]]
name = "gated"
pool = "linux"
argv = ["sh", "-c", '{gated}']
timeout_seconds = 300
[presets.env]
RCM_MARK_DIR = "{marks}"
"""


def expected_count() -> bytes:
    body = b"".join(f"line-{i}\n".encode() for i in range(LINES))
    return body + b"tail-without-newline\n"  # 러너가 줄 끝을 붙인다


@pytest.fixture
def grace(tmp_path: Path):
    g = Grace(tmp_path)
    with g.server.config.open("a") as f:
        f.write(EXTRA)
        f.write(GATED_TOML.format(gated=GATED, marks=g.marks))
    yield g
    g.teardown()


def server_log(grace: Grace, jid: int) -> bytes:
    return (grace.server.data_dir / "jobs" / str(jid) / "log.txt").read_bytes()


@pytest.mark.slow
def test_rl6_b_rl16_a_partition_mid_job_and_the_server_log_is_byte_equal(grace):
    grace.start("build-02", relay=True)
    jid = grace.submit("count")
    grace.wait_state(jid, ("running",), 30)
    time.sleep(1.5)
    assert grace.relay is not None
    grace.relay.pause()  # 응답을 붙잡는다 — 보낸 조각의 답을 잃는다
    time.sleep(4)
    grace.relay.resume()
    assert grace.wait_exit(jid, 120) == 0
    got = server_log(grace, jid)
    assert hashlib.sha256(got).hexdigest() == hashlib.sha256(expected_count()).hexdigest()
    doc = grace.job(jid)
    assert doc["log_complete"] is True and doc["late_result"] is None


@pytest.mark.slow
def test_rl17_a_the_last_line_without_a_newline_survives_the_job_end(grace):
    grace.start("build-02")
    jid = grace.submit("count")
    assert grace.wait_exit(jid, 120) == 0
    assert server_log(grace, jid) == expected_count()
    out = rcm("logs", str(jid), env=grace.env)
    assert out.returncode == 0 and "tail-without-newline" in out.stdout
    assert grace.job(jid)["log_complete"] is True


# ── RL14 a — finish 를 디스크에 둔 채 kill -9 ─────────────────────────────────


def queued_finish(data_dir: Path, jid: int) -> dict[str, Any] | None:
    """세션 폴더의 그 잡 `outbox.json` 에 적힌 finish(없으면 None)."""
    for path in data_dir.glob(f"worker-sessions/*/jobs/{jid}/outbox.json"):
        try:
            doc = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(doc.get("finish"), dict):
            return doc["finish"]
    return None


@pytest.mark.slow
def test_rl14_a_a_finish_left_on_disk_by_kill_9_is_sent_before_the_restart_registers(grace):
    """RL14 (a): 잡 X 가 끝나 finish 를 디스크 큐에 썼지만 서버에 못 보낸 채 워커가 `kill -9` 로
    죽는다. 같은 데이터 폴더 · 같은 토큰으로 다시 띄운 워커가 등록 전에 옛 세션으로 그 finish 를
    보내고, X 는 진짜 결과(종료 5 → failed)로 닫힌다. 등록이 X 를 먼저 lost 로 닫지 않았으므로
    `late_result` 도 없다(§4.4 의 7 · 결정 268 · 326)."""
    (old,) = grace.start("build-02", relay=True)
    assert grace.relay is not None and old.proc is not None
    jid = grace.submit("gated")
    grace.wait_state(jid, ("running",), 30)
    wait_for_log = time.monotonic() + 20
    while time.monotonic() < wait_for_log and b"before" not in _log_or_empty(grace, jid):
        time.sleep(0.1)
    # 연결 거부 — 이제 finish 는 서버에 닿지 않는다. `pause()` 가 아닌 것은 붙잡힌 옛 프로세스의
    # 요청 바이트가 `resume()` 때 서버로 넘어가 시험을 흐리지 않게 하려는 것이다
    grace.relay.stop()
    (grace.marks / f"go.{jid}").touch()
    deadline = time.monotonic() + 30
    fin = None
    while time.monotonic() < deadline and fin is None:
        fin = queued_finish(old.data_dir, jid)
        time.sleep(0.1)
    assert fin is not None, old.log_text()
    assert (fin["outcome"], fin["exit_code"]) == ("failed", 5), fin
    time.sleep(0.5)
    os.kill(old.proc.pid, signal.SIGKILL)
    old.proc.wait(10)
    assert grace.job(jid)["state"] == "running"  # 서버는 아직 결과를 모른다
    # 같은 서버 주소여야 같은 주인의 폴더다(`owner.json` 은 설정의 server 를 해시한다, 결정 325)
    grace.relay.resume()
    again = GWorker(
        grace.server, "build-02", grace.tokens["build-02"], grace.tmp, grace.relay.url, 0
    )
    assert again.data_dir == old.data_dir
    grace.workers.append(again)
    again.start(grace.client, timeout=30)
    assert grace.wait_exit(jid, 60) == 1, (again.log_text(), grace.job(jid))
    doc = grace.job(jid)
    assert (doc["state"], doc["exit_code"]) == ("failed", 5), doc
    assert doc["late_result"] is None and doc["log_complete"] is True, doc
    assert server_log(grace, jid) == b"before\nafter\n"
    assert "Traceback" not in again.log_text()


def _log_or_empty(grace: Grace, jid: int) -> bytes:
    try:
        return server_log(grace, jid)
    except OSError:
        return b""


# ── RL18 a~c — 릴리스 비밀이 조각 경계 · 재전송을 넘어도 가려진다 ───────────────

#: 워커의 조각 상한을 줄여 띄운다 — 진짜 `rcm worker` 이고 `LOG_BATCH_BYTES` 만 다르다
WRAPPER = """\
import runpy
import sys

from remote_ci_monitor import remote_worker

remote_worker.LOG_BATCH_BYTES = int(sys.argv.pop(1))
sys.argv[0] = "rcm"
runpy.run_module("remote_ci_monitor.cli", run_name="__main__", alter_sys=True)
"""
PIECE = 200
SECRET_LINES = 600
GEN = """\
import sys
import time

secret = open(sys.argv[1]).read()
for i in range(int(sys.argv[2])):
    line = f"k{i}={secret} end" if i % 3 == 0 else f"pad-{i:05d}-" + "x" * (i % 37)
    sys.stdout.write(line + "\\n")
    sys.stdout.flush()
    time.sleep(0.02)
"""
RELEASE_TOML = """
[[presets]]
name = "release-plan"
pool = "linux"
repo = "app"
source_modes = ["tree", "git_ref"]
argv = ["{python}", "{gen}", "{secret_file}", "{lines}"]
timeout_seconds = 300

[[presets]]
name = "release-upload"
pool = "linux"
repo = "app"
source_modes = ["git_ref"]
argv = ["true"]
timeout_seconds = 60

[[presets]]
name = "release-review"
pool = "linux"
repo = "app"
source_modes = ["git_ref"]
argv = ["true"]
timeout_seconds = 60

[[repos]]
name = "app"
url = "{repo}"

[repos.app.release]
secrets_dir_env = "APP_SECRETS"

[repos.app.release.presets]
plan = "release-plan"
upload = "release-upload"
review = "release-review"

[[repos.app.release.secrets]]
name = "GH_TOKEN"
kind = "value"
"""
PROFILE = {
    "secrets_dir_env": "APP_SECRETS",
    "presets": {"plan": "release-plan", "upload": "release-upload", "review": "release-review"},
    "secrets": [{"name": "GH_TOKEN", "kind": "value"}],
}


def gen_output(secret: str, masked: bool) -> bytes:
    shown = "****" if masked else secret
    out = []
    for i in range(SECRET_LINES):
        line = f"k{i}={shown} end" if i % 3 == 0 else f"pad-{i:05d}-" + "x" * (i % 37)
        out.append(line + "\n")
    return "".join(out).encode()


@pytest.mark.slow
def test_rl18_a_b_c_a_release_secret_never_reaches_the_server_log(grace):
    """RL18 (a)~(c): 서버가 아는 릴리스 비밀이 한 조각 안 · 조각 경계 근처 · 재전송된 조각에 섞여도
    `log.txt` 와 `rcm logs` 와 잡 문서 어디에도 원문이 없다. 조각을 200 바이트로 줄여 비밀 줄이
    여러 경계에 걸리게 하고, 서버가 받은 조각의 답을 중계에서 끊어 같은 범위를 다시 보내게 한다.
    `log.txt` 는 비밀을 `****` 로 바꾼 출력과 바이트로 같다 — 빠짐 · 겹침도 없다."""
    secret = "rl18-" + secrets.token_hex(16)  # 실행마다 새로 만든다(레포에 값이 없다)
    tmp = grace.tmp
    secret_file = tmp / "secret.txt"
    secret_file.write_text(secret)
    gen = tmp / "gen.py"
    gen.write_text(GEN)
    wrapper = tmp / "small_pieces.py"
    wrapper.write_text(WRAPPER)
    with grace.server.config.open("a") as f:
        f.write(
            RELEASE_TOML.format(
                python=sys.executable,
                gen=gen,
                secret_file=secret_file,
                lines=SECRET_LINES,
                repo=tmp / "no-such-repo.git",
            )
        )
    store = SecretStore(
        secrets_dir(grace.server.config, "app"), parse_release_profile("app", PROFILE)
    )
    store.put("GH_TOKEN", secret.encode(), content_type="text/plain")
    grace.start(relay=True)
    assert grace.relay is not None
    w = GWorker(
        grace.server, "build-02", grace.tokens["build-02"], tmp, grace.relay.url, len(grace.workers)
    )
    w.argv[: len(RCM)] = [sys.executable, str(wrapper), str(PIECE)]
    grace.workers.append(w)
    w.start(grace.client)
    jid = grace.submit("release-plan")
    grace.wait_state(jid, ("running",), 30)
    for _ in range(2):  # (c) 서버가 받은 조각의 답을 끊는다 — 워커는 같은 범위를 다시 보낸다
        seen = grace.relay.slow_count
        grace.relay.slow_next(f"/worker/jobs/{jid}/log", every=1.0)
        assert grace.relay.wait_slow(20, count=seen + 1), w.log_text()
        time.sleep(0.7)
        grace.relay.stop()
        time.sleep(0.3)
        grace.relay.release_slow()
        grace.relay.resume()
        time.sleep(0.5)
    assert grace.wait_exit(jid, 120) == 0, w.log_text()
    got = server_log(grace, jid)
    assert secret.encode() not in got
    assert got == gen_output(secret, masked=True)  # (a) · (b) 가렸고 (c) 겹침 · 빠짐이 없다
    out = rcm("logs", str(jid), env=grace.env)
    assert out.returncode == 0 and secret not in out.stdout and "k0=**** end" in out.stdout
    doc = grace.job(jid)
    assert secret not in json.dumps(doc) and doc["log_complete"] is True
    text = w.log_text()
    assert f"#{jid} log: " in text and "retrying" in text, text  # 재전송이 실제로 있었다


# ── RL12 b — 버전을 따라 exec 해도 디스크 큐가 남는다 ─────────────────────────


@pytest.fixture(scope="module")
def bootloader(tmp_path_factory):
    return make_bootloader(tmp_path_factory.mktemp("boot") / "venv")


@pytest.mark.slow
@pytest.mark.skipif(os.name != "posix", reason="follow is POSIX only (결정 172)")
def test_rl12_b_a_report_left_by_the_drain_exec_is_sent_before_the_new_image_registers(
    tmp_path, bootloader
):
    """RL12 (b): 드레인 중 끝난 잡의 finish 가 서버 오류로 `DRAIN_REPORT_SECONDS`(60초) 안에 못
    가면 워커는 큐를 디스크에 둔 채 같은 PID 로 exec 한다. 새 이미지는 등록 전에 옛 세션의 큐를
    보내고, 잡은 진짜 결과로 닫히며 새 세션의 등록이 그 상태를 바꾸지 않는다(§4.4 「M5q exec 의
    드레인 조건」 · 결정 255 · 260)."""
    stack = Stack(tmp_path, bootloader)
    marks = tmp_path / "marks"
    marks.mkdir()
    add_presets(stack, marks)
    app = stack.srv.app
    finish, register = app.worker_finish, app.worker_register
    failing = {"on": False}
    events: list[tuple[str, float, Any]] = []

    def worker_finish(token: str, job_id: int, body: Any) -> Any:
        if failing["on"]:
            events.append(("finish", time.monotonic(), 503))
            raise ApiError(503, "busy")
        out = finish(token, job_id, body)
        events.append(("finish", time.monotonic(), 200))
        return out

    def worker_register(token: str, body: Any) -> Any:
        events.append(("register", time.monotonic(), body.get("version")))
        return register(token, body)

    app.worker_finish = worker_finish  # type: ignore[method-assign]
    app.worker_register = worker_register  # type: ignore[method-assign]
    log = tmp_path / "worker.log"
    sup = Supervisor(stack, log)
    try:
        stack.wait_registered(INSTALLED)
        p = sup.spawned[0]
        jid = stack.srv.queued_job(preset="hold")
        wait_for(lambda: (marks / f"started.{jid}").exists(), "hold started", log)
        failing["on"] = True
        stack.bump(NEW)
        wait_for(lambda: DRAINING in log.read_text(), "draining", log)
        (marks / f"go.{jid}").touch()
        anyway = f"job #{jid} still not reported — restarting anyway"
        wait_for(lambda: anyway in log.read_text(), "the drain limit", log, timeout=120)
        failing["on"] = False
        stack.wait_registered(NEW, timeout=60)
        job = stack.srv.store.get_job(jid)
        assert job.state == SUCCEEDED and job.exit_code == 0, (job.state, job.summary)
        ok = [at for kind, at, st in events if kind == "finish" and st == 200]
        new_reg = [at for kind, at, v in events if kind == "register" and v == NEW]
        assert len(ok) == 1 and new_reg and ok[0] < new_reg[0], events
        assert any(st == 503 for kind, _, st in events if kind == "finish"), events
        assert len(sup.spawned) == 1 and p.poll() is None, log.read_text()  # 같은 PID 의 exec
        time.sleep(1.5)  # 새 세션의 heartbeat 몇 번 뒤에도
        assert stack.srv.store.get_job(jid).state == SUCCEEDED
    finally:
        sup.close()
        stack.close()
