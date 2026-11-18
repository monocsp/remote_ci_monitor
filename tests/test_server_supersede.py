"""M5o A — 서버에서 supersede: 잡이 `queued` 가 되는 두 자리 · 원격 워커 · 상태 JSON · CLI.

명세 `docs/m5o-workplan.md` §2, 시나리오 `docs/m5o-test-scenarios-a.md`.

- 서버는 `test_worker_api.WorkerServer`(주입 시계 · 로컬 워커 스레드 없음). 원격 워커는 HTTP
  로 흉내 낸다.
- tree 잡은 `branch` 를 실어 제출한다. git_ref 잡은 `resolve_ref` 를 바꿔 끼워 해석마다 새 sha 를
  준다(같은 브랜치에 push 가 또 온 것).
- 로컬 레인에서 도는 잡(A2)과 CLI 는 `test_server.Server` 를 쓴다(진짜 프로세스 · 벽시계).
"""

from __future__ import annotations

import io
import itertools
import json
import socket
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from remote_ci_monitor import config as rcm_config
from remote_ci_monitor import server as rcm_server
from remote_ci_monitor.cli import main
from remote_ci_monitor.config import parse_preset
from remote_ci_monitor.core.model import (
    CANCELLED,
    CANCELLING,
    QUEUED,
    RUNNING,
    SUCCEEDED,
    UPLOADING,
)
from remote_ci_monitor.store import Store
from test_server import TAR, Server, sh
from test_server_m5 import blobs_for, manifest_of
from test_worker_api import GRACE, HEARTBEAT, WORKER_PRESETS, WorkerServer

_trees = (f"{i:064x}" for i in itertools.count(1))


def presets(mode: str = "all", **extra: Any) -> list[dict[str, Any]]:
    """`WORKER_PRESETS` 에 supersede 를 켠 프리셋 셋을 더한다."""
    return [
        *WORKER_PRESETS,
        sh(
            "g",
            "echo g",
            supersede=mode,
            inputs=[{"name": "scope", "type": "choice", "choices": ["full", "fast"]}],
            **extra,
        ),
        sh("g-off", "echo off"),
        sh(
            "d",
            "echo d",
            supersede=mode,
            source_modes=["tree", "git_ref"],
            repo="app",
        ),
    ]


@pytest.fixture
def srv(tmp_path):
    s = WorkerServer(tmp_path)
    s.cfg.presets = tuple(parse_preset(p) for p in presets("all"))
    yield s
    s.close()


def use_mode(srv: WorkerServer, mode: str) -> None:
    srv.cfg.presets = tuple(parse_preset(p) for p in presets(mode))


def submit(
    srv,
    token: str = "alice",
    *,
    preset: str = "g",
    branch: str | None = "feat/x",
    repo: str | None = "org/app",
    inputs: dict[str, Any] | None = None,
    tree_hash: str | None = None,
    **extra: Any,
) -> tuple[int, dict[str, Any]]:
    body: dict[str, Any] = {
        "preset": preset,
        "inputs": {"scope": "full"} if inputs is None and preset == "g" else (inputs or {}),
        "source": {
            "mode": "tree",
            "repo": repo,
            "branch": branch,
            "base_sha": "abc123f",
            "dirty": True,
            "tree_hash": tree_hash or next(_trees),
            "bytes": len(TAR),
        },
        "requester_label": f"{token}@host",
        **extra,
    }
    return srv.req("POST", "/jobs", token=token, json_body=body)


def new_tree(srv, token: str = "alice", **kw: Any) -> int:
    status, resp = submit(srv, token, **kw)
    assert status == 201, resp
    return int(resp["job_id"])


def queued_tree(srv, token: str = "alice", **kw: Any) -> int:
    jid = new_tree(srv, token, **kw)
    status, resp = srv.req("PUT", f"/jobs/{jid}/tree", token=token, body=TAR)
    assert status == 200 and resp["state"] == QUEUED, resp
    return jid


def state(srv, jid: int) -> str:
    return srv.store.get_job(jid).state


def assert_superseded(srv, old: int, new: int) -> None:
    j = srv.store.get_job(old)
    assert j.state == CANCELLED, j.state
    assert j.cancelled_by == "superseded"
    assert j.summary == f"superseded by #{new}"
    assert j.summary_code == "superseded" and j.summary_args == {"by": new}


# ── A1: tree — 업로드가 끝날 때 ──────────────────────────────────────────────


def test_a1_tree_upload_finish_supersedes_the_queued_old_job(srv):
    o = queued_tree(srv)
    n = queued_tree(srv)
    assert_superseded(srv, o, n)
    assert state(srv, n) == QUEUED
    # 상세와 상태 JSON 이 같은 값을 말한다
    assert srv.view(o)["superseded_by"] == n
    assert srv.view(n)["superseded_by"] is None
    recent = [r for p in srv.status()["pools"] for r in p["recent"] if r["id"] == o]
    assert recent and recent[0]["superseded_by"] == n
    assert srv.row(n)["superseded_by"] is None


def test_a1_cached_upload_with_nothing_missing_supersedes(tmp_path):
    srv = WorkerServer(tmp_path)
    srv.cfg.presets = tuple(parse_preset(p) for p in presets("all"))
    try:
        files = {"a.txt": b"hello\n"}
        o = queued_tree(srv)
        # 첫 잡이 blob 을 올려 두면 둘째 잡의 manifest 는 빠진 것이 없다 → manifest 자리에서 queued
        first = new_tree(srv)
        _s, m = srv.req(
            "POST", f"/jobs/{first}/tree/manifest", token="alice", json_body=manifest_of(files)
        )
        assert m["missing"], m
        status, resp = srv.req(
            "PUT",
            f"/jobs/{first}/tree",
            token="alice",
            body=blobs_for(files, m["missing"]),
            headers={"X-RCM-Tree": "blobs"},
        )
        assert status == 200, resp
        assert_superseded(srv, o, first)  # blob 자리
        second = new_tree(srv)
        _s, m2 = srv.req(
            "POST", f"/jobs/{second}/tree/manifest", token="alice", json_body=manifest_of(files)
        )
        assert m2["missing"] == [] and m2["state"] == QUEUED
        assert_superseded(srv, first, second)  # manifest 자리
    finally:
        srv.close()


# ── A1: git_ref — 제출 때 ────────────────────────────────────────────────────


@pytest.fixture
def shas(monkeypatch):
    """ref 이름 → sha. 같은 이름을 다시 해석하면 새 커밋(push)이 된다."""
    counter = itertools.count(1)

    def fake(url, ref, *, timeout, credential=None):
        return f"{next(counter):040x}"

    monkeypatch.setattr(rcm_server, "resolve_ref", fake)


def git_ref(srv, token: str = "alice", ref: str = "feat/x", **extra: Any):
    body = {
        "preset": "d",
        "inputs": {},
        "source": {"mode": "git_ref", "ref": ref},
        "requester_label": f"{token}@host",
        **extra,
    }
    return srv.req("POST", "/jobs", token=token, json_body=body)


def test_a1_git_ref_submit_supersedes(srv, shas):
    s1, o = git_ref(srv)
    s2, n = git_ref(srv)
    assert s1 == s2 == 201, (o, n)
    assert_superseded(srv, o["job_id"], n["job_id"])


def test_a7_tree_and_git_ref_with_the_same_branch_are_left(srv, shas):
    o = queued_tree(srv, preset="d", branch="main")
    status, n = git_ref(srv, ref="main")
    assert status == 201, n
    assert state(srv, o) == QUEUED


# ── A3 · A4 · A26 · A27 · A30: 도는 잡 ───────────────────────────────────────


def running_remote(srv, worker: str = "build-02", **kw: Any) -> int:
    srv.registered(worker)
    jid = queued_tree(srv, **kw)
    assert srv.claimed(worker) == jid
    assert state(srv, jid) == RUNNING
    return jid


def test_a3_waiting_mode_leaves_the_running_job(srv):
    use_mode(srv, "waiting")
    o = running_remote(srv)
    n = queued_tree(srv)
    assert state(srv, o) == RUNNING
    assert state(srv, n) == QUEUED


def test_a4_remote_worker_gets_the_cancel_through_heartbeat(srv):
    o = running_remote(srv)
    n = queued_tree(srv)
    j = srv.store.get_job(o)
    assert j.state == CANCELLING and j.cancel.by == "superseded"
    # 큐 행이 이미 말한다(A30)
    assert srv.row(o)["superseded_by"] == n
    status, hb = srv.heartbeat("build-02", jobs=[o])
    assert status == 200 and o in hb["cancel"], hb
    status, done = srv.finish("build-02", o, "cancelled", exit_code=None)
    assert status == 200, done
    assert_superseded(srv, o, n)
    assert srv.view(o)["superseded_by"] == n


def test_a26_unconfirmed_cancel_keeps_the_superseded_summary(srv):
    o = running_remote(srv)
    n = queued_tree(srv)
    srv.clock.advance(GRACE + 2 * HEARTBEAT + 1)
    status, _hb = srv.heartbeat("build-02", jobs=[o])
    assert status == 200
    assert_superseded(srv, o, n)


def test_a27_job_that_finished_on_its_own_is_not_superseded(srv):
    o = running_remote(srv)
    queued_tree(srv)
    status, _ = srv.finish("build-02", o, "succeeded", exit_code=0)
    assert status == 200
    assert state(srv, o) == SUCCEEDED
    assert srv.view(o)["superseded_by"] is None


def test_a14_three_old_jobs(srv):
    use_mode(srv, "off")  # 치우지 않고 쌓는다 — 도는 잡 하나 + 대기 둘
    r = running_remote(srv)
    q1 = queued_tree(srv)
    q2 = queued_tree(srv)
    use_mode(srv, "all")
    n = queued_tree(srv)
    assert state(srv, r) == CANCELLING
    assert srv.store.get_job(r).summary_args == {"by": n}
    assert_superseded(srv, q1, n)
    assert_superseded(srv, q2, n)


# ── A5 · A6 · A9 · A12 · A13 · A31: 남는다 ───────────────────────────────────


def test_a5_other_inputs_are_left(srv):
    o = queued_tree(srv, inputs={"scope": "full"})
    queued_tree(srv, inputs={"scope": "fast"})
    assert state(srv, o) == QUEUED


@pytest.mark.parametrize("branch", [None, ""])
def test_a6_detached_tree_is_left(srv, branch):
    o = queued_tree(srv, branch=branch)
    queued_tree(srv, branch=branch)
    assert state(srv, o) == QUEUED


def test_a8_same_tree_joins_and_nothing_is_superseded(srv):
    tree = next(_trees)
    o = queued_tree(srv, tree_hash=tree)
    status, resp = submit(srv, tree_hash=tree)
    assert status == 200 and resp["joined"] is True and resp["job_id"] == o
    assert state(srv, o) == QUEUED


def test_a8_same_tree_without_join_is_left(srv):
    tree = next(_trees)
    o = queued_tree(srv, tree_hash=tree)
    queued_tree(srv, tree_hash=tree, join=False)
    assert state(srv, o) == QUEUED


def test_a9_old_job_with_someone_elses_joiner_is_left(srv):
    tree = next(_trees)
    o = queued_tree(srv, tree_hash=tree)
    status, resp = submit(srv, "bob", tree_hash=tree)
    assert resp["joined"] is True
    queued_tree(srv)
    assert state(srv, o) == QUEUED


def test_a31_other_requester_is_left(srv):
    o = queued_tree(srv, "bob")
    queued_tree(srv, "alice")
    assert state(srv, o) == QUEUED


def test_a12_no_supersede_on_a_tree_job_survives_until_the_upload_ends(srv):
    o = queued_tree(srv)
    n = new_tree(srv, supersede=False)
    status, resp = srv.req("PUT", f"/jobs/{n}/tree", token="alice", body=TAR)
    assert status == 200, resp
    assert state(srv, o) == QUEUED and state(srv, n) == QUEUED


def test_a12_no_supersede_on_a_git_ref_job(srv, shas):
    _s, o = git_ref(srv)
    _s, n = git_ref(srv, supersede=False)
    assert state(srv, o["job_id"]) == QUEUED


@pytest.mark.parametrize("value", ["no", 0, None, []])
def test_a28_supersede_must_be_a_bool(srv, value):
    status, resp = submit(srv, supersede=value)
    assert status == 400 and "supersede" in resp["error"], resp


def test_a13_preset_off_leaves_everything(srv):
    o = queued_tree(srv, preset="g-off")
    queued_tree(srv, preset="g-off")
    assert state(srv, o) == QUEUED


def test_a29_submitter_cannot_turn_it_on(srv):
    o = queued_tree(srv, preset="g-off")
    queued_tree(srv, preset="g-off", supersede=True)
    assert state(srv, o) == QUEUED


# ── A10 · A11 · A22: 업로드 ─────────────────────────────────────────────────


def test_a10_interrupted_upload_leaves_the_old_job(srv):
    o = queued_tree(srv)
    n = new_tree(srv)
    sock = socket.create_connection(("127.0.0.1", srv.port), timeout=5)
    head = (
        f"PUT /jobs/{n}/tree HTTP/1.1\r\nHost: x\r\n"
        f"Authorization: Bearer {srv.tokens['alice']}\r\n"
        f"Content-Length: {len(TAR) + 5000}\r\n\r\n"
    ).encode()
    sock.sendall(head + TAR[:100])
    time.sleep(0.2)
    sock.close()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and state(srv, n) == UPLOADING:
        time.sleep(0.05)
    assert state(srv, n) == CANCELLED
    assert state(srv, o) == QUEUED


def test_a10_rejected_blob_upload_leaves_the_old_job(tmp_path):
    srv = WorkerServer(tmp_path)
    srv.cfg.presets = tuple(parse_preset(p) for p in presets("all"))
    try:
        o = queued_tree(srv)
        n = new_tree(srv)
        files = {"b.txt": b"world\n"}
        _s, m = srv.req(
            "POST", f"/jobs/{n}/tree/manifest", token="alice", json_body=manifest_of(files)
        )
        assert m["missing"]
        status, _ = srv.req(
            "PUT",
            f"/jobs/{n}/tree",
            token="alice",
            body=b"garbage",
            headers={"X-RCM-Tree": "blobs"},
        )
        assert status == 400
        assert state(srv, n) == CANCELLED
        assert state(srv, o) == QUEUED
    finally:
        srv.close()


def test_a11_restart_during_the_new_upload_leaves_the_old_job(srv):
    o = queued_tree(srv)
    n = new_tree(srv)
    assert state(srv, n) == UPLOADING
    srv.store.recover_on_start(srv.clock())
    assert state(srv, n) == CANCELLED
    assert state(srv, o) == QUEUED


def test_a22_uploading_old_job_is_superseded_and_its_upload_is_refused(srv):
    o = new_tree(srv)  # 아직 업로드 중
    n = queued_tree(srv)
    assert_superseded(srv, o, n)
    status, resp = srv.req("PUT", f"/jobs/{o}/tree", token="alice", body=TAR)
    assert status == 409, resp
    # 409 본문이 직접 말한다 — `rcm run` 은 업로드 실패 자리에서 다시 조회하지 않는다
    assert resp["state"] == CANCELLED and resp["superseded_by"] == n


# ── A15: 우선순위 ────────────────────────────────────────────────────────────


def test_a15_new_job_inherits_high_priority(srv):
    o = queued_tree(srv)
    assert srv.store.set_priority(o, 1, srv.clock())  # `rcm bump` 와 같은 자리
    n = queued_tree(srv)
    assert srv.store.get_job(n).priority == 1


# ── A20: reload 은 소급하지 않는다 ───────────────────────────────────────────


def test_a20_reload_off_to_all_applies_from_the_next_queued_job():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        path = tmp / "rcm.toml"
        head = f'[server]\ndata_dir = "{tmp}"\n'
        preset = '[[presets]]\nname = "g"\nargv = ["true"]\n'
        path.write_text(head + preset, encoding="utf-8")
        cfg = rcm_config.load_server_config(path)
        store = Store(cfg.data_dir / "db.sqlite3")
        app = rcm_server.App(cfg, store)
        try:
            secret = store.add_token("alice-laptop", admin=False, now=app.now_fn())
            token = store.verify_token(secret)

            def queue_one() -> int:
                body = {
                    "preset": "g",
                    "inputs": {},
                    "source": {
                        "mode": "tree",
                        "repo": "org/app",
                        "branch": "feat/x",
                        "tree_hash": next(_trees),
                        "bytes": len(TAR),
                    },
                }
                _code, resp = app.submit(body, token)
                job = app.begin_upload(resp["job_id"], token, len(TAR))
                app.receive_upload(job, io.BytesIO(TAR), len(TAR))
                return int(resp["job_id"])

            o1 = queue_one()
            o2 = queue_one()  # off — 둘 다 남는다
            assert store.get_job(o1).state == QUEUED
            path.write_text(head + preset + 'supersede = "all"\n', encoding="utf-8")
            out = app.reload_config()
            assert out["presets_changed"] == ["g"]
            # reload 자체는 아무것도 치우지 않는다
            assert store.get_job(o1).state == QUEUED and store.get_job(o2).state == QUEUED
            n = queue_one()
            assert store.get_job(o1).summary_args == {"by": n}
            assert store.get_job(o2).summary_args == {"by": n}
        finally:
            for t in threading.enumerate():
                if t.name == "rcm-measure":
                    t.join(timeout=30)
            store.close()


# ── A17: 두 새 잡이 같은 순간에 queued ──────────────────────────────────────


def test_a17_two_uploads_finishing_together_leave_only_the_newer(srv):
    o = queued_tree(srv)
    n1 = new_tree(srv)
    n2 = new_tree(srv)
    barrier = threading.Barrier(2)
    codes: dict[int, int] = {}

    def up(jid: int) -> None:
        barrier.wait()
        codes[jid] = srv.req("PUT", f"/jobs/{jid}/tree", token="alice", body=TAR)[0]

    ts = [threading.Thread(target=up, args=(j,)) for j in (n1, n2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(15)
    assert codes[n2] == 200
    assert state(srv, n2) == QUEUED
    assert state(srv, n1) == CANCELLED and state(srv, o) == CANCELLED
    assert srv.store.get_job(n1).summary_args == {"by": n2}


# ── A2: 로컬 레인에서 도는 잡 ────────────────────────────────────────────────


def test_a2_local_lane_running_job_is_cancelled_through_the_normal_path(tmp_path):
    s = Server(tmp_path, workers=False)
    try:
        s.cfg.presets = (
            parse_preset(sh("slow", "echo '::rcm::step::wait'; sleep 30", supersede="all")),
        )
        s.app.start()

        def go() -> int:
            body = {
                "preset": "slow",
                "inputs": {},
                "source": {
                    "mode": "tree",
                    "repo": "org/app",
                    "branch": "feat/x",
                    "tree_hash": next(_trees),
                    "bytes": len(TAR),
                },
            }
            status, resp = s.req("POST", "/jobs", token="alice", json_body=body)
            assert status == 201, resp
            assert s.upload(resp["job_id"])[0] == 200
            return int(resp["job_id"])

        o = go()
        s.wait_state(o, "running")
        n = go()
        done = s.wait_terminal(o)
        assert done.state == CANCELLED
        assert done.summary == f"superseded by #{n}"
        assert done.cancelled_by == "superseded"
        s.req("POST", f"/jobs/{n}/cancel", token="alice", json_body={})
        s.wait_terminal(n)
    finally:
        s.close()


# ── A19 · A22 · A36: CLI ────────────────────────────────────────────────────


@pytest.fixture
def env(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("RCM_LABEL", raising=False)

    def use(server, token: str = "alice") -> None:
        monkeypatch.setenv("RCM_SERVER", f"http://127.0.0.1:{server.port}")
        monkeypatch.setenv("RCM_TOKEN", server.tokens[token])

    return use


def last_json(out: str) -> dict[str, Any]:
    lines = [ln for ln in out.strip().splitlines() if ln.startswith("{")]
    assert lines, out
    return json.loads(lines[-1])


def test_a19_rcm_wait_on_a_superseded_job(srv, env, capsys):
    env(srv)
    o = queued_tree(srv)
    n = queued_tree(srv)
    code = main(["wait", "--job", str(o), "--poll"])
    cap = capsys.readouterr()
    assert code == 2
    assert f"#{o} superseded by #{n} — rcm wait --job {n}" in cap.err
    doc = last_json(cap.out)
    assert doc["superseded_by"] == n and doc["state"] == CANCELLED


def test_a19_rcm_wait_on_a_plain_cancel_has_no_superseded_line(srv, env, capsys):
    env(srv)
    o = queued_tree(srv, preset="g-off")
    srv.cancel(o)
    code = main(["wait", "--job", str(o), "--poll"])
    cap = capsys.readouterr()
    assert code == 2 and "superseded" not in cap.err
    assert last_json(cap.out)["superseded_by"] is None


def test_a36_client_sends_supersede_false_only_when_asked(monkeypatch):
    from remote_ci_monitor.client import Client

    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(Client, "post_json", lambda self, path, body: sent.append(body) or {})
    c = Client("http://127.0.0.1:1", "t")
    c.submit("g", {}, {"mode": "tree"}, requester_label=None)
    c.submit("g", {}, {"mode": "tree"}, requester_label=None, supersede=False)
    assert "supersede" not in sent[0]
    assert sent[1]["supersede"] is False


def test_a36_rcm_run_no_supersede_reaches_the_server(tmp_path, env, capsys, monkeypatch, shas):
    from remote_ci_monitor.client import Client, ClientError
    from test_server_m3 import make_server

    s = make_server(
        tmp_path,
        [sh("d", "echo d", source_modes=["git_ref"], repo="app", supersede="all")],
        workers=False,
        repos=(rcm_config.RepoConfig(name="app", url=str(tmp_path / "nowhere.git")),),
    )
    try:
        env(s)
        seen: list[dict[str, Any]] = []

        def capture(self, *args: Any, **kwargs: Any):
            seen.append(kwargs)
            raise ClientError(400, "stop here")

        monkeypatch.setattr(Client, "submit", capture)
        main(["run", "d", "--ref", "main", "--no-supersede"])
        main(["run", "d", "--ref", "main"])
        capsys.readouterr()
        assert seen[0]["supersede"] is False
        assert seen[1].get("supersede", True) is True
    finally:
        s.close()


def test_a22_rcm_run_whose_upload_was_superseded_exits_2(srv, env, capsys, monkeypatch, tmp_path):
    from remote_ci_monitor.client import Client, ClientError

    env(srv)
    work = tmp_path / "work"
    work.mkdir()
    (work / "a.txt").write_text("a\n")
    looked: list[int] = []
    monkeypatch.setattr(Client, "job", lambda self, jid, **kw: looked.append(jid) or {})

    def superseded(self, job_id, *args, **kwargs):
        body = {"error": "job was cancelled during upload", "state": CANCELLED}
        raise ClientError(409, body["error"], {**body, "superseded_by": job_id + 1})

    monkeypatch.setattr(Client, "upload", superseded)
    code = main(["run", "g", "-f", "scope=full", "--dir", str(work), "--no-cache"])
    cap = capsys.readouterr()
    assert code == 2, cap.err
    jid = last_json(cap.out)["job_id"]
    assert f"#{jid} superseded by #{jid + 1} — rcm wait --job {jid + 1}" in cap.err
    assert last_json(cap.out)["superseded_by"] == jid + 1
    assert "upload failed" not in cap.err
    assert looked == []


# ── 독립 검증에서 나온 것 ────────────────────────────────────────────────────


def test_joining_a_job_being_superseded_makes_a_new_job(srv):
    """alice 의 도는 잡이 그녀의 새 잡에 밀려 취소 중일 때 bob 이 같은 트리를 내면 합류가 아니라
    새 잡이다 — 합류했다면 bob 의 대기가 alice 의 새 잡을 가리키며 종료 2 로 끝난다(결정 113)."""
    tree = next(_trees)
    o = running_remote(srv, tree_hash=tree)
    queued_tree(srv)  # alice 의 새 push — o 가 cancelling
    assert state(srv, o) == CANCELLING
    status, resp = submit(srv, "bob", tree_hash=tree)
    assert status == 201, resp
    assert resp["joined"] is False and resp["job_id"] != o
    assert srv.store.get_job(o).joiners == ()


def test_joining_a_job_a_person_is_cancelling_makes_a_new_job(srv):
    """M5o 전부터의 동작을 고친 것 — 사람이 취소 중인 잡도 결과를 줄 수 없다."""
    tree = next(_trees)
    o = running_remote(srv, preset="g-off", tree_hash=tree)
    srv.cancel(o)
    assert state(srv, o) == CANCELLING
    status, resp = submit(srv, "bob", preset="g-off", tree_hash=tree)
    assert status == 201 and resp["joined"] is False, resp


def test_two_repos_without_a_name_do_not_supersede_each_other(srv):
    o = queued_tree(srv, repo=None)
    queued_tree(srv, repo=None)
    assert state(srv, o) == QUEUED


def test_a_job_sent_to_one_machine_supersedes_nothing(srv):
    """같은 요청자(admin) · 같은 브랜치 — 새 잡이 `--machine` 이라는 것 하나만 다르다."""
    srv.registered("build-02")
    o = queued_tree(srv, "admin")
    status, resp = submit(srv, "admin", machine="build-02")
    assert status == 201, resp
    status, up = srv.req("PUT", f"/jobs/{resp['job_id']}/tree", token="admin", body=TAR)
    assert status == 200, up
    assert state(srv, o) == QUEUED
    # 대조군: 지정하지 않은 새 잡은 o 를 치운다
    n = queued_tree(srv, "admin")
    assert_superseded(srv, o, n)


def test_strict_cancel_tokens_turn_supersede_off(tmp_path):
    srv = WorkerServer(tmp_path, cancel_requires_submission_token=True)
    srv.cfg.presets = tuple(parse_preset(p) for p in presets("all"))
    logged: list[str] = []
    srv.app.log = logged.append  # type: ignore[method-assign]
    try:
        o = queued_tree(srv)
        n = queued_tree(srv)
        assert state(srv, o) == QUEUED and state(srv, n) == QUEUED
        assert f"job {n}: supersede skipped: cancel_requires_submission_token" in logged
        assert logged.count(f"job {n}: supersede skipped: cancel_requires_submission_token") == 1
        status, h = srv.req("GET", "/api/health")
        assert sorted(h["supersede_skipped_presets"]) == ["d", "g"]
    finally:
        srv.close()


def test_health_lists_no_skipped_presets_normally(srv):
    status, h = srv.req("GET", "/api/health")
    assert h["supersede_skipped_presets"] == []


def test_no_supersede_mark_is_forgotten_when_the_upload_dies(srv):
    n = new_tree(srv, supersede=False)
    assert n in srv.app._no_supersede
    sock = socket.create_connection(("127.0.0.1", srv.port), timeout=5)
    head = (
        f"PUT /jobs/{n}/tree HTTP/1.1\r\nHost: x\r\n"
        f"Authorization: Bearer {srv.tokens['alice']}\r\n"
        f"Content-Length: {len(TAR) + 5000}\r\n\r\n"
    ).encode()
    sock.sendall(head + TAR[:100])
    time.sleep(0.2)
    sock.close()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and state(srv, n) == UPLOADING:
        time.sleep(0.05)
    assert state(srv, n) == CANCELLED
    assert n not in srv.app._no_supersede


def test_no_supersede_mark_is_forgotten_when_the_job_is_cancelled(srv):
    n = new_tree(srv, supersede=False)
    srv.cancel(n)
    assert n not in srv.app._no_supersede
