"""루프백 e2e(M5a) — 진짜 `rcm serve` + `rcm run`:
`--priority high` 잡이 대기 중인 normal 잡보다 먼저 돈다(완료 기준 ①) · 같은 트리 두 번째
`rcm run` 은 캐시로 거의 안 보낸다(완료 기준 ②) · `[[notify]]` argv 규칙이 잡마다 한 번 돈다(③).
명세는 docs/m5-workplan.md M5a. 아직 구현 전이라 빨간 것이 정상이다.

test_e2e_loopback 의 ServerProc 를 쓰되 설정(레인 1 · 캐시 · [[notify]])만 바꾼다.
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path

import pytest

from test_e2e_loopback import ServerProc, last_json, rcm

SERVER_TOML = """
[server]
bind = "127.0.0.1"
port = {port}
data_dir = "{data_dir}"
grace_seconds = 1
lanes = 1
snapshot_cache = true

[[notify]]
name = "file-hook"
argv = ["sh", "-c", "echo $RCM_JOB_ID:$RCM_STATE:$RCM_PRESET:$RCM_NOTIFY >> {notify_log}"]
timeout_seconds = 10

[[presets]]
name = "gate"
description = "Loopback gate"
argv = ["sh", "scripts/gate.sh"]
timeout_seconds = 60
expected_seconds = 5
duration_key_inputs = ["scope"]
[[presets.inputs]]
name = "scope"
type = "choice"
choices = ["full", "fast", "slow"]
default = "full"
"""

GATE_SH = """#!/bin/sh
echo "::rcm::step::run"
echo "scope=$RCM_INPUT_SCOPE job=$RCM_JOB_ID"
case "$RCM_INPUT_SCOPE" in
  slow) sleep 4;;
esac
test -f assets/blob.bin && wc -c < assets/blob.bin
echo "::rcm::summary::done $RCM_INPUT_SCOPE"
exit 0
"""


class M5Server(ServerProc):
    def __init__(self, tmp_path: Path):
        super().__init__(tmp_path)
        self.notify_log = tmp_path / "notified.txt"
        self.config.write_text(
            SERVER_TOML.format(port=self.port, data_dir=self.data_dir, notify_log=self.notify_log)
        )


@pytest.fixture
def server(tmp_path):
    s = M5Server(tmp_path)
    yield s
    s.stop()


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "tree"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "gate.sh").write_text(GATE_SH)
    (root / "README.md").write_text("loopback m5\n")
    return root


def job_json(env: dict, job_id: int) -> dict:
    out = rcm("wait", "--job", str(job_id), "--timeout", "0", env=env)
    return last_json(out.stdout)


def wait_state(env: dict, job_id: int, states: tuple[str, ...], timeout: float = 20) -> dict:
    deadline = time.monotonic() + timeout
    body = job_json(env, job_id)
    while time.monotonic() < deadline:
        body = job_json(env, job_id)
        if body["state"] in states:
            return body
        time.sleep(0.2)
    raise AssertionError(f"job {job_id} is {body['state']}, wanted {states}")


def iso(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def notify_lines(server: M5Server) -> list[str]:
    if not server.notify_log.exists():
        return []
    return [ln for ln in server.notify_log.read_text().splitlines() if ln.strip()]


def test_high_priority_runs_first_and_every_job_notifies_once(server, tree):
    token = server.token("laptop")
    admin = server.token("admin", admin=True)
    server.start()
    env = server.client_env(token)
    admin_env = server.client_env(admin)

    # 레인 1 을 4초짜리 잡이 잡는다
    first = rcm("run", "gate", "-f", "scope=slow", "--no-wait", env=env, cwd=tree)
    assert first.returncode == 0, first.stderr
    slow_id = last_json(first.stdout)["job_id"]
    wait_state(env, slow_id, ("running",))
    # 그 뒤에 normal 하나, 그리고 high 하나 — 비-admin 은 프리셋 기본(normal)보다 못 올린다
    normal = rcm("run", "gate", "-f", "scope=fast", "--no-wait", env=env, cwd=tree)
    assert normal.returncode == 0, normal.stderr
    normal_id = last_json(normal.stdout)["job_id"]
    refused = rcm("run", "gate", "--priority", "high", "--no-wait", env=env, cwd=tree)
    assert refused.returncode == 2 and "admin" in refused.stderr, refused.stderr
    high = rcm("run", "gate", "--priority", "high", "--no-wait", env=admin_env, cwd=tree)
    assert high.returncode == 0, high.stderr
    high_id = last_json(high.stdout)["job_id"]
    assert high_id != normal_id
    # 대기 중인 큐: high 가 normal 앞에 선다(`rcm top --json` 의 queue[].priority · position)
    doc = json.loads(rcm("top", "--json", env=env).stdout)
    rows = {r["id"]: r for r in doc["pools"][0]["queue"]}
    assert rows[high_id]["priority"] == 1 and rows[normal_id]["priority"] == 0
    if rows[high_id]["state"] == "queued" and rows[normal_id]["state"] == "queued":
        assert rows[high_id]["position"] == 1 and rows[normal_id]["position"] == 2
    # 둘 다 끝날 때까지 — high 가 먼저 시작했어야 한다
    high_done = last_json(rcm("wait", "--job", str(high_id), env=env).stdout)
    normal_done = last_json(rcm("wait", "--job", str(normal_id), env=env).stdout)
    slow_done = last_json(rcm("wait", "--job", str(slow_id), env=env).stdout)
    assert (high_done["state"], normal_done["state"], slow_done["state"]) == (
        "succeeded",
        "succeeded",
        "succeeded",
    )
    started = [iso(j["started_at"]) for j in (slow_done, high_done, normal_done)]
    assert started == sorted(started)  # slow → high → normal (시각은 초 단위라 같을 수 있다)
    assert iso(high_done["started_at"]) >= iso(slow_done["finished_at"])  # 레인 1: 순서대로
    assert iso(normal_done["started_at"]) >= iso(high_done["finished_at"])  # high 가 먼저 끝났다

    # 완료 기준 ③: 알림 명령이 잡마다 정확히 한 번
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and len(notify_lines(server)) < 3:
        time.sleep(0.2)
    time.sleep(1.0)  # 더 오는지 본다
    lines = notify_lines(server)
    assert sorted(lines) == sorted(
        f"{jid}:succeeded:gate:file-hook" for jid in (slow_id, normal_id, high_id)
    ), lines
    top = rcm("top", "--json", env=env)
    assert json.loads(top.stdout)["server"]["notify_failures"] == 0
    assert json.loads(top.stdout)["server"]["last_error"] is None


def test_second_rcm_run_on_the_same_tree_uses_the_cache(server, tree):
    token = server.token("laptop")
    server.start()
    env = server.client_env(token)
    (tree / "assets").mkdir()
    (tree / "assets" / "blob.bin").write_bytes(os.urandom(1_000_000))  # 압축 안 되는 1 MB

    first = rcm("run", "gate", "-f", "scope=fast", env=env, cwd=tree)
    assert first.returncode == 0, first.stderr
    body = last_json(first.stdout)
    assert body["state"] == "succeeded" and body["summary"] == "done fast"
    assert body["source"]["uploaded_bytes"] >= 1_000_000  # 첫 업로드는 다 보낸다
    assert body["source"]["cached_bytes"] == 0
    log = server.data_dir / "jobs" / str(body["id"]) / "log.txt"
    assert "1000000" in log.read_text()  # blob 에서 자재화한 파일이 워크스페이스에 있었다

    second = rcm("run", "gate", "-f", "scope=fast", "--no-join", env=env, cwd=tree)
    assert second.returncode == 0, second.stderr
    body2 = last_json(second.stdout)
    assert body2["id"] != body["id"] and body2["state"] == "succeeded"
    assert "cache" in second.stderr, second.stderr  # `uploading #N: 0.0 / 1.0 MB (cache 100%)`
    assert body2["source"]["uploaded_bytes"] <= 4096, body2["source"]  # 0 이거나 아주 작다
    assert body2["source"]["cached_bytes"] >= 1_000_000
    assert not (server.data_dir / "jobs" / str(body2["id"]) / "tree.tar.gz").exists()
    log2 = server.data_dir / "jobs" / str(body2["id"]) / "log.txt"
    assert "1000000" in log2.read_text()

    third = rcm("run", "gate", "-f", "scope=fast", "--no-join", "--no-cache", env=env, cwd=tree)
    assert third.returncode == 0, third.stderr
    body3 = last_json(third.stdout)
    assert body3["state"] == "succeeded"
    assert body3["source"]["uploaded_bytes"] >= 1_000_000  # 옛 경로: 전체 tar
    assert (server.data_dir / "jobs" / str(body3["id"]) / "tree.tar.gz").exists()
    assert "cache" not in third.stderr

    top = json.loads(rcm("top", "--json", env=env).stdout)
    cache = top["server"]["snapshot_cache"]
    assert cache["blobs"] >= 1 and cache["bytes"] >= 1_000_000
    assert top["server"]["last_error"] is None
