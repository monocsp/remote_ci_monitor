"""e2e(M3) — 실제 프로세스로 신호·그룹을 잠근다: 그룹 직렬화(레인 2, 다른 레인은 놀지 않는다) ·
취소가 손자까지 죽인다 · TERM 을 무시하는 스크립트는 grace 뒤 KILL · 타임아웃도 같은 경로.
명세는 docs/m3-workplan.md §3.

in-process 서버(test_server.Server)에 워커를 띄워 진짜 `sh` 를 돌린다. 시각은 스크립트가 파일로
찍고, 기다림은 전부 마감 있는 폴링이다(맨 sleep 은 0.05초 간격뿐).
"""

import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from remote_ci_monitor.core.status import parse_iso
from test_server import Server
from test_server_m3 import make_server

# macOS 의 date 는 %N 을 모른다 — python 으로 epoch 초를 찍는다(PY 는 프리셋 env 로 준다)
STAMP = '"$PY" -c "import time; print(repr(time.time()))"'
MARK = (
    f'{STAMP} > "$RCM_MARK_DIR/$RCM_JOB_ID.start"; sleep 1; '
    f'{STAMP} > "$RCM_MARK_DIR/$RCM_JOB_ID.end"'
)


def sh(name: str, script: str, **extra: Any) -> dict[str, Any]:
    return {"name": name, "argv": ["sh", "-c", script], "timeout_seconds": 60, **extra}


def presets(marks: Path) -> list[dict[str, Any]]:
    env = {"RCM_MARK_DIR": str(marks), "PY": sys.executable}
    return [
        sh("qa", MARK, concurrency_group="devices", env=env),
        sh("solo", MARK, env=env),
        sh("orphan", 'sleep 300 & echo $! > "$RCM_MARK_DIR/pid"; wait', env=env),
        sh("stubborn", "trap '' TERM; sleep 300", env=env),
        sh("stubborn-timeout", "trap '' TERM; sleep 300", timeout_seconds=1, env=env),
    ]


def start_server(tmp_path: Path, *, workers: bool, **overrides: Any) -> tuple[Server, Path]:
    marks = tmp_path / "marks"
    marks.mkdir()
    return make_server(tmp_path, presets(marks), workers=workers, **overrides), marks


def submit_tree(srv: Server, preset: str, n: int) -> int:
    """tree 잡 하나를 올려 queued 로 둔다. n 으로 tree_hash 를 달리해 합류를 피한다."""
    jid = srv.submit(preset=preset, tree_hash=f"{n:02x}" * 32)[1]["job_id"]
    assert srv.upload(jid)[0] == 200
    return jid


def read_mark(marks: Path, jid: int, kind: str) -> float:
    return float((marks / f"{jid}.{kind}").read_text().strip())


def wait_until(pred: Callable[[], Any], timeout: float, what: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


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


def test_group_serializes_while_the_other_lane_keeps_working(tmp_path):
    s, marks = start_server(tmp_path, workers=False, lanes=2)
    try:
        first = submit_tree(s, "qa", 1)
        second = submit_tree(s, "qa", 2)
        solo = submit_tree(s, "solo", 3)
        s.app.start()  # 두 레인이 같이 깨어난다
        for jid in (first, second, solo):
            assert s.wait_terminal(jid, timeout=20).state == "succeeded", jid
        t = {
            j: (read_mark(marks, j, "start"), read_mark(marks, j, "end"))
            for j in (first, second, solo)
        }
        assert t[first][1] - t[first][0] >= 0.9, t
        assert t[second][0] >= t[first][1], t  # 같은 그룹 → 첫 잡이 끝난 뒤에야 시작
        assert t[solo][0] < t[first][1], t  # 그룹 없는 잡은 두 번째 레인에서 병행(레인이 안 논다)
        v1 = s.req("GET", f"/jobs/{first}")[1]
        v2 = s.req("GET", f"/jobs/{second}")[1]
        running2 = next(tr["at"] for tr in v2["transitions"] if tr["state"] == "running")
        assert parse_iso(running2) >= parse_iso(v1["finished_at"])
    finally:
        s.close()


def test_cancel_kills_the_grandchildren_of_the_job_process(tmp_path):
    s, marks = start_server(tmp_path, workers=True)
    try:
        jid = submit_tree(s, "orphan", 1)
        s.wait_state(jid, "running")
        pidfile = marks / "pid"
        wait_until(lambda: pidfile.exists() and pidfile.read_text().strip(), 5.0, "pid file")
        pid = int(pidfile.read_text().strip())
        assert not is_dead(pid)
        t0 = time.monotonic()
        status, body = s.req("POST", f"/jobs/{jid}/cancel", token="alice", json_body={})
        assert status == 200 and body["state"] == "cancelling"
        j = s.wait_terminal(jid, timeout=10)
        assert j.state == "cancelled" and j.summary == "cancelled by alice-laptop"
        assert time.monotonic() - t0 < 5.0
        wait_until(lambda: is_dead(pid), 5.0, f"grandchild {pid} to die")
    finally:
        s.close()


def test_term_ignoring_script_is_killed_after_grace(tmp_path):
    s, _ = start_server(tmp_path, workers=True)  # grace_seconds = 1 (test_server.Server 기본)
    try:
        jid = submit_tree(s, "stubborn", 1)
        s.wait_state(jid, "running")
        t0 = time.monotonic()
        status, body = s.req("POST", f"/jobs/{jid}/cancel", token="alice", json_body={})
        assert status == 200 and body["state"] == "cancelling"
        view = s.req("GET", f"/jobs/{jid}")[1]  # grace 1초 안에는 아직 cancelling
        assert view["state"] == "cancelling" and view["reason"] == "cancelling", view["state"]
        cancel = view["cancel"]
        assert cancel["by"] == "alice-laptop"
        requested = parse_iso(cancel["requested_at"])
        kill_at = parse_iso(cancel["kill_at"])
        assert (kill_at - requested).total_seconds() == 1  # kill_at = 요청 + grace
        j = s.wait_terminal(jid, timeout=10)
        took = time.monotonic() - t0
        assert j.state == "cancelled" and took < 4.0, (j.state, took)
        assert j.exit_code == -9  # TERM 은 무시됐고 KILL 이 죽였다
        fin = s.req("GET", f"/jobs/{jid}")[1]
        assert fin["cancelled_by"] == "alice-laptop"  # 최근 완료 행 — cancel 은 cancelling 동안만
        states = [t["state"] for t in fin["transitions"]]
        assert states == ["uploading", "queued", "running", "cancelling", "cancelled"], states
        at = {t["state"]: parse_iso(t["at"]) for t in fin["transitions"]}
        gap = (at["cancelled"] - at["cancelling"]).total_seconds()
        assert 1 <= gap <= 4, gap
    finally:
        s.close()


def test_timeout_with_term_ignored_ends_timed_out_with_the_limit_summary(tmp_path):
    s, _ = start_server(tmp_path, workers=True)
    try:
        jid = submit_tree(s, "stubborn-timeout", 1)
        s.wait_state(jid, "running")
        t0 = time.monotonic()
        j = s.wait_terminal(jid, timeout=10)
        took = time.monotonic() - t0
        assert j.state == "timed_out" and j.summary == "limit 1s" and j.timeout_seconds == 1
        assert j.exit_code == -9 and took < 4.5, (j.exit_code, took)
        fin = s.req("GET", f"/jobs/{jid}")[1]
        assert fin["job_seconds"] is not None and fin["job_seconds"] <= 4
        assert [t["state"] for t in fin["transitions"]] == [
            "uploading",
            "queued",
            "running",
            "timed_out",
        ]
        assert fin["cancelled_by"] is None
    finally:
        s.close()
