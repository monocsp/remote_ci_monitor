"""워커 세션 · 펜싱 · 프로토콜 2(M5t-b §4.2 · §4.7).

결정 255 · 258 · 261 · 262 · 265 · 336 · 339-a 를 잠근다.

서버는 in-process HTTP(`test_worker_api.WorkerServer`, 주입 시계)다. 워커 이름마다 한 프로세스를
흉내 내고 `new_process` 가 같은 토큰의 새 프로세스를 만든다. 세션 id 는 실행 중에 만든다(§8).
"""

from __future__ import annotations

import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from remote_ci_monitor import __version__, cli
from remote_ci_monitor import remote_worker as rw
from remote_ci_monitor.client import Client, ClientError
from remote_ci_monitor.config import WorkerConfig, disconnect_grace, parse_preset
from remote_ci_monitor.core.model import CANCELLING, LOST, QUEUED, RUNNING, SUCCEEDED
from remote_ci_monitor.remote_worker import RemoteWorker, WorkerExit
from test_worker_api import TIMEOUT, WORKER_PRESETS, WorkerServer, sh

PROTOCOL_TEXT = "worker protocol 2 required: upgrade rcm on this worker"
EXIT_PROTOCOL = 3  # 프로토콜 불일치 — 감독자가 되살린다(결정 258)
PRESETS = [
    *WORKER_PRESETS,
    sh("ship", "echo ship", irreversible=True),  # 그룹 없음 → irreversible:ship · global
]


@pytest.fixture
def srv(tmp_path):
    s = WorkerServer(tmp_path, presets=PRESETS)
    yield s
    s.close()


def job_of(srv: WorkerServer, jid: int):
    return srv.store.get_job(jid)


def hold_rows(srv: WorkerServer) -> list[dict[str, Any]]:
    return [dict(r) for r in srv.store._conn().execute("SELECT * FROM irreversible_holds")]


def running(srv: WorkerServer, worker: str = "build-02", *, preset: str = "ok", lane: int = 1):
    jid = srv.queued_job(preset=preset)
    assert srv.claimed(worker, lane) == jid
    return jid


# ── 펜싱(T8 · W5 · RL10 · RL11) ────────────────────────────────────────────────


def test_rl10_a_long_poll_claim_fenced_while_it_waits_takes_no_job(srv, monkeypatch):
    """A 의 claim 이 긴 대기 중일 때 B 가 등록해 A 를 펜싱한다. 그 뒤 들어온 잡을 A 의 대기가
    집으면 받을 사람이 없는 잡이 G 동안 `running` 이고, 표식이면 돈 적 없는 명령에 홀드가 걸린다.
    claim 트랜잭션이 세션의 펜싱을 다시 보고 409 `session_fenced` 로 끝낸다(P3 1라운드 MED)."""
    srv.registered("build-02")
    old = srv.session("build-02")
    real_claim = srv.store.claim
    entered = threading.Event()

    def first_try_seen(*args: Any, **kw: Any):
        entered.set()
        return real_claim(*args, **kw)

    monkeypatch.setattr(srv.store, "claim", first_try_seen)
    out: dict[str, Any] = {}

    def wait_for_a_job() -> None:
        body = {"lane": 1, "wait_seconds": 8, "worker_session_id": old, "claim_seq": 1}
        out["answer"] = srv.req("POST", "/worker/claim", token="build-02", json_body=body)

    t = threading.Thread(target=wait_for_a_job, daemon=True)
    t.start()
    assert entered.wait(5)  # A 는 이미 대기에 들어갔다(첫 시도는 빈 큐)
    srv.new_process("build-02")
    srv.registered("build-02")  # B — A 를 펜싱한다
    jid = srv.queued_job(preset="ship")
    t.join(15)
    assert not t.is_alive()
    status, body = out["answer"]
    assert (status, (body or {}).get("code")) == (409, "session_fenced"), body
    assert job_of(srv, jid).state == QUEUED and hold_rows(srv) == []
    assert srv.claimed("build-02") == jid  # B 가 집는다


def test_rl10_a_second_process_fences_the_first_and_the_first_can_only_confirm(srv):
    srv.registered("build-02", lanes=2)
    jid = running(srv)
    old = srv.new_process("build-02")  # 같은 토큰으로 프로세스 B
    srv.registered("build-02", lanes=2)
    rows = (
        srv.store._conn()
        .execute(
            "SELECT id, fenced_at, fenced_by FROM worker_sessions WHERE worker_name='build-02'"
        )
        .fetchall()
    )
    current = [r["id"] for r in rows if r["fenced_at"] is None]
    assert current == [srv.session("build-02")]  # B 가 유일한 현재 세션
    assert {r["id"]: r["fenced_by"] for r in rows}[old] == srv.session("build-02")
    # 확인 전까지 X 는 busy — 그룹 · 레인이 풀리지 않고 B 의 그 레인 claim 은 204
    assert job_of(srv, jid).state == RUNNING
    srv.queued_job()
    assert srv.claim("build-02", 1)[0] == 204
    # A 의 다음 요청은 무엇이든 409 session_fenced(job_closed 와 구별)
    a = {"worker_session_id": old}
    for status, body in (
        srv.req(
            "POST",
            "/worker/heartbeat",
            token="build-02",
            json_body={**a, "seq": 9, "claims_settled": 1, "jobs": [jid]},
        ),
        srv.req(
            "POST",
            f"/worker/jobs/{jid}/phase",
            token="build-02",
            json_body={**a, "phase": "executing"},
        ),
        srv.req(
            "POST",
            f"/worker/jobs/{jid}/finish",
            token="build-02",
            json_body={**a, "outcome": "failed", "exit_code": 1},
        ),
        srv.req(
            "POST",
            f"/worker/jobs/{jid}/log",
            token="build-02",
            body=b"x\n",
            headers={"Content-Type": "application/octet-stream", "X-RCM-Worker-Session": old},
        ),
    ):
        assert status == 409 and body["code"] == "session_fenced", body
    assert job_of(srv, jid).state == RUNNING
    # 좁은 확인 finish 만 받는다 — lost + worker_replaced, group_gone 참이면 홀드 0
    status, body = srv.req(
        "POST",
        f"/worker/jobs/{jid}/finish",
        token="build-02",
        json_body={
            **a,
            "outcome": "lost",
            "exit_code": None,
            "summary_code": "worker_replaced",
            "summary_args": {},
            "group_gone": True,
        },
    )
    assert status == 200, body
    j = job_of(srv, jid)
    assert j.state == LOST and j.summary_code == "worker_replaced"
    assert j.summary_args == {"name": "build-02"}
    assert hold_rows(srv) == []
    # 펜싱된 세션의 재등록은 409 session_fenced — 핑퐁 없음
    status, body = srv.register("build-02", worker_session_id=old, lanes=2)
    assert status == 409 and body["code"] == "session_fenced"


@pytest.mark.parametrize("learned_first", [True, False])
@pytest.mark.parametrize(("outcome", "rc"), [(SUCCEEDED, 0), ("failed", 1)])
def test_gap2_a_fenced_sessions_natural_end_goes_as_the_narrow_confirm(
    srv, tmp_path, learned_first, outcome, rc
):
    """# GAP2 펜싱된 세션의 표식 잡이 제 힘으로 끝나도(성공 · 실패) 그 결과는 기록되지 않고 좁은
    확인(`lost` + `worker_replaced` + `group_gone`)으로 간다(결정 261). 펜싱을 먼저 알았든
    (heartbeat) 이 finish 의 409 로 처음 알았든 같다. 그러지 않으면 서버가 409 로 거절하고
    워커는 종료 0 이라 잡은 G 뒤에 `worker_unreachable` 로 닫히고 그룹이 홀드된다."""
    import types

    srv.registered("build-02")
    jid = running(srv, preset="ship")
    old = srv.new_process("build-02")
    srv.registered("build-02")  # 같은 토큰의 새 프로세스가 옛 세션을 펜싱했다
    url = f"http://127.0.0.1:{srv.port}"
    cfg = WorkerConfig(
        server=url, token=srv.tokens["build-02"], data_dir=str(tmp_path / "w"), lanes=1
    )
    w = RemoteWorker(cfg, log=lambda m: None)
    w.session_id = old
    w._bind_session(w.client)
    if learned_first:
        w._on_fenced("heartbeat")
    observer = types.SimpleNamespace(irreversible=True, closed_state=None, finish_sent=False)
    w._finish(jid, outcome, rc, "done", observer, group_gone=True)
    j = job_of(srv, jid)
    assert (j.state, j.summary_code) == (LOST, "worker_replaced")
    assert hold_rows(srv) == []
    assert w.fenced.is_set()


def test_p62_3_a_narrow_confirm_that_cannot_be_sent_is_retried_and_keeps_the_job_folder(
    srv, tmp_path, monkeypatch
):
    """# P6-2 #3 finish 의 409 로 펜싱을 처음 안 워커는 결과 대신 좁은 확인을 보낸다. 그 확인이
    연결 실패면 다른 보고처럼 다시 보내고, 끝내 못 보내면 잡 폴더를 지우지 않는다 — 다음
    프로세스가 그 폴더를 판정해 확인을 보낸다. 지우면 확인이 사라지고 잡은 G 뒤
    `worker_unreachable` 로 닫히며 표식 잡의 그룹은 홀드된다."""
    import types

    # M5t-b PR2: 보고는 잡마다 보내는 스레드의 백오프로 다시 보내고 멈추는 중에는 정한 창까지 한다
    monkeypatch.setattr(rw, "BACKOFF_START_SECONDS", 0.01)
    monkeypatch.setattr(rw, "BACKOFF_MAX_SECONDS", 0.01)
    monkeypatch.setattr(rw, "STOP_REPORT_SECONDS", 0.3)
    srv.registered("build-02")
    jid = running(srv, preset="ship")
    old = srv.new_process("build-02")
    srv.registered("build-02")  # 같은 토큰의 새 프로세스가 옛 세션을 펜싱했다
    url = f"http://127.0.0.1:{srv.port}"
    cfg = WorkerConfig(
        server=url, token=srv.tokens["build-02"], data_dir=str(tmp_path / "w"), lanes=1
    )
    w = RemoteWorker(cfg, log=lambda m: None)
    w.session_id = old
    w._bind_session(w.client)
    w.session_dir = tmp_path / "w" / rw.SESSIONS_DIR / old
    folder = w._job_dir(jid)
    assert folder is not None
    w._write_claim(jid, True, 600)
    w._write_proc(jid, None)
    real = w.client.finish
    sent: list[str] = []

    def unreachable_after_the_first(job_id: int, outcome: str, *a: Any, **kw: Any) -> Any:
        sent.append(outcome)
        if len(sent) > 1:
            raise ClientError(0, "connection refused")
        return real(job_id, outcome, *a, **kw)

    monkeypatch.setattr(w.client, "finish", unreachable_after_the_first)
    observer = types.SimpleNamespace(irreversible=True, closed_state=None, finish_sent=False)
    w._finish(jid, SUCCEEDED, 0, "done", observer, group_gone=True)
    assert w.fenced.is_set()  # 첫 finish 의 409 로 알았다
    assert sent[0] == SUCCEEDED and sent[1:] == [LOST] * len(sent[1:])
    assert len(sent) >= 3  # 좁은 확인을 한 번이 아니라 다른 보고처럼 다시 보냈다
    assert job_of(srv, jid).state == RUNNING
    assert (folder / "claim.json").is_file()  # 보내지 못했다 — 다음 프로세스가 판정한다


def test_p63_1_an_unsent_finish_while_stopping_keeps_the_job_folder_and_parks_nothing(
    srv, tmp_path, monkeypatch
):
    """# P6-3 #1 펜싱이 아닌 멈춤(SIGTERM · 드레인)에서 finish 를 끝내 보내지 못하면 미룰 곳이
    없다 — 미뤄 둔 보고는 멈춘 프로세스와 함께 사라진다. 그래서 `_unreported` 에 넣지 않고
    잡 폴더(`claim.json`)를 남긴다. 다음 프로세스가 이 세션을 죽은 세션으로 판정해 확인을
    보낸다. 지우면 확인이 사라지고 표식 잡의 그룹은 G 뒤 홀드된다."""
    import types

    monkeypatch.setattr(rw, "BACKOFF_START_SECONDS", 0.01)
    monkeypatch.setattr(rw, "BACKOFF_MAX_SECONDS", 0.01)
    monkeypatch.setattr(rw, "STOP_REPORT_SECONDS", 0.3)
    srv.registered("build-02")
    jid = running(srv, preset="ship")
    url = f"http://127.0.0.1:{srv.port}"
    cfg = WorkerConfig(
        server=url, token=srv.tokens["build-02"], data_dir=str(tmp_path / "w"), lanes=1
    )
    w = RemoteWorker(cfg, log=lambda m: None)
    w.session_id = srv.session("build-02")
    w._bind_session(w.client)
    w.session_dir = tmp_path / "w" / rw.SESSIONS_DIR / w.session_id
    folder = w._job_dir(jid)
    assert folder is not None
    w._write_claim(jid, True, 600)
    w._write_proc(jid, None)
    sent: list[str] = []

    def unreachable(job_id: int, outcome: str, *a: Any, **kw: Any) -> Any:
        sent.append(outcome)
        raise ClientError(0, "connection refused")

    monkeypatch.setattr(w.client, "finish", unreachable)
    w.stopping.set()  # 멈추는 중 — 펜싱은 아니다
    observer = types.SimpleNamespace(irreversible=True, closed_state=None, finish_sent=False)
    w._finish(jid, SUCCEEDED, 0, "done", observer, group_gone=True)
    assert not w.fenced.is_set()
    assert sent and set(sent) == {SUCCEEDED}  # 결과를 보내려 했고 좁은 확인으로 바꾸지 않았다
    assert w._senders == {}  # 멈추는 프로세스는 메모리에 남기지 않는다 — 큐는 디스크에 있다
    assert jid not in w.running
    assert (folder / "claim.json").is_file()  # 보내지 못했다 — 다음 프로세스가 판정한다
    assert job_of(srv, jid).state == RUNNING


@pytest.mark.parametrize("gone", [True, None])
def test_p62_1_a_remote_job_with_a_retry_frozen_before_the_mark_is_not_retried(tmp_path, gone):
    """# P6-2 #1 표식 전에 `[retry]` 로 제출된 잡은 반복 정책을 얼린 채 대기한다. reload 가
    `irreversible = true` 를 붙인 뒤 원격 워커가 claim 하면 claim 이 표식을 얼린다. 그 잡의 75
    finish 를 `retried` 로 닫으면 다음 시도가 같은 명령을 다시 돌린다 — 표식 잡은 반복하지
    않는다. `failed` 로 닫히고 다음 시도가 없다. 홀드는 `group_gone` 대로다."""
    from remote_ci_monitor.core.model import FAILED

    retry = {"on_exit": 75, "interval_seconds": 60, "max_attempts": 3}
    deploy = sh("deploy", "echo deploy", source_modes=["git_ref"], repo="app", retry=retry)
    presets = [p for p in PRESETS if p["name"] != "deploy"]
    srv = WorkerServer(tmp_path, presets=[*presets, deploy])
    try:
        jid = srv.git_ref_job()
        assert job_of(srv, jid).retry_on_exit == 75  # 제출 때 얼었다
        # reload: 표식을 달면서 `[retry]` 를 뺐다(둘을 함께 두면 로더가 거절한다, P6 GAP4)
        marked = parse_preset(
            sh("deploy", "echo deploy", source_modes=["git_ref"], repo="app", irreversible=True)
        )
        srv.cfg.presets = (*(p for p in srv.cfg.presets if p.name != "deploy"), marked)
        srv.app._sync_irreversible()
        srv.registered("build-02")
        assert srv.claimed("build-02") == jid
        assert job_of(srv, jid).irreversible is True  # claim 때 얼었다
        body: dict[str, Any] = {"outcome": "failed", "exit_code": 75}
        if gone is not None:
            body["group_gone"] = gone
        status, answer = srv.req(
            "POST", f"/worker/jobs/{jid}/finish", token="build-02", json_body=body
        )
        assert (status, answer.get("state")) == (200, FAILED), answer
        j = job_of(srv, jid)
        assert (j.state, j.exit_code, j.retried_as) == (FAILED, 75, None)
        assert srv.store.list_scheduled() == []  # 다음 시도가 없다
        assert len(hold_rows(srv)) == (0 if gone else 1)
    finally:
        srv.close()


@pytest.mark.parametrize("kind", ["irreversible_frozen_retry", "plain"])
def test_p63_3_a_result_reported_while_cancelling_closes_like_any_job_that_does_not_retry(
    tmp_path, kind
):
    """# P6-3 #3 M5s 결정 225 는 `retried` 만 `running` 에서 받는다 — 취소가 먼저 왔으면 다음
    시도를 만들지 않고 `cancelled` 로 닫는다. 반복하지 않는 잡이 실행까지 가서 낸 결과는
    `cancelling` 에서도 그대로 받는다(remote_workers.py 의 `only_from` 주석). 표식 잡으로 claim
    된 잡은 얼린 `[retry]` 가 있어도 반복하지 않으므로(P6-2 #1) 그 75 는 반복하지 않는 잡의
    실패와 같다 — `failed` 로 닫히고 다음 시도가 없다."""
    from remote_ci_monitor.core.model import FAILED

    retry = {"on_exit": 75, "interval_seconds": 60, "max_attempts": 3}
    deploy = sh("deploy", "echo deploy", source_modes=["git_ref"], repo="app", retry=retry)
    presets = [p for p in PRESETS if p["name"] != "deploy"]
    srv = WorkerServer(tmp_path, presets=[*presets, deploy])
    try:
        srv.registered("build-02")
        if kind == "plain":
            jid, rc = running(srv), 1
        else:
            jid, rc = srv.git_ref_job(), 75
            marked = parse_preset(
                sh("deploy", "echo deploy", source_modes=["git_ref"], repo="app", irreversible=True)
            )
            srv.cfg.presets = (*(p for p in srv.cfg.presets if p.name != "deploy"), marked)
            srv.app._sync_irreversible()
            assert srv.claimed("build-02") == jid
            assert job_of(srv, jid).irreversible is True
            assert job_of(srv, jid).retry_on_exit == 75
        assert srv.cancel(jid)[1]["state"] == CANCELLING
        body = {"outcome": "failed", "exit_code": rc, "group_gone": True}
        status, answer = srv.req(
            "POST", f"/worker/jobs/{jid}/finish", token="build-02", json_body=body
        )
        assert (status, answer.get("state")) == (200, FAILED), answer
        j = job_of(srv, jid)
        assert (j.state, j.exit_code, j.retried_as) == (FAILED, rc, None)
        assert srv.store.list_scheduled() == []
        assert hold_rows(srv) == []
    finally:
        srv.close()


def test_gap6_an_artifact_upload_fenced_while_it_streams_publishes_nothing(srv, monkeypatch):
    """# GAP6 산출물 PUT 은 받기 전에 세션을 한 번 본다. 바이트를 받는 동안 같은 토큰의 새
    프로세스가 그 세션을 펜싱하면, 받은 뒤 발행하기 전에 다시 보고 버린다 — 409
    `session_fenced` 이고 묶음 행 · 파일이 남지 않는다(펜싱된 세션은 아무것도 바꾸지 못한다)."""
    import io
    import tarfile

    from remote_ci_monitor import remote_workers

    srv.registered("build-02")
    jid = running(srv)
    old = srv.session("build-02")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        data = b"hello\n"
        info = tarfile.TarInfo("out/a.txt")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    body = buf.getvalue()
    real = remote_workers._stream_to_file

    def fenced_midway(stream: Any, part: Path, length: int) -> str:
        digest = real(stream, part, length)
        srv.new_process("build-02")
        srv.registered("build-02")  # 바이트가 다 온 뒤 · 발행 전에 펜싱된다
        return digest

    monkeypatch.setattr(remote_workers, "_stream_to_file", fenced_midway)
    status, answer = srv.req(
        "PUT",
        f"/worker/jobs/{jid}/artifacts",
        token="build-02",
        body=body,
        headers={"Content-Type": "application/x-tar", "X-RCM-Worker-Session": old},
    )
    row = srv.store.get_bundle(jid) or {}
    assert (status, answer.get("code")) == (409, "session_fenced")
    assert row.get("bundle_sha256") is None
    assert not (srv.cfg.data_dir / "artifacts" / str(jid) / "bundle.tar").exists()


def test_rl10_an_irreversible_job_confirmed_without_group_gone_holds(srv):
    srv.registered("build-02")
    jid = running(srv, preset="ship")
    old = srv.new_process("build-02")
    srv.registered("build-02")
    status, body = srv.req(
        "POST",
        f"/worker/jobs/{jid}/finish",
        token="build-02",
        json_body={
            "worker_session_id": old,
            "outcome": "lost",
            "exit_code": None,
            "summary_code": "worker_replaced",
            "group_gone": False,
        },
    )
    assert status == 200, body
    assert [(h["job_id"], h["cause"], h["concurrency_group"]) for h in hold_rows(srv)] == [
        (jid, "group_not_gone", "irreversible:ship")
    ]
    # RL44 (e): 홀드가 남는다. 그 뒤 group_gone: true 좁은 확인은 홀드를 푼다(확인 2)
    status, body = srv.req(
        "POST",
        f"/worker/jobs/{jid}/finish",
        token="build-02",
        json_body={
            "worker_session_id": old,
            "outcome": "lost",
            "exit_code": None,
            "summary_code": "worker_replaced",
            "group_gone": True,
        },
    )
    assert status == 200 and body["hold_released"] is True, body
    (h,) = hold_rows(srv)
    assert h["release_kind"] == "worker" and h["released_by"] == "build-02"


def test_rl11_unknown_foreign_and_malformed_sessions(srv):
    srv.registered("build-02")
    srv.registered("build-03")
    jid = running(srv)
    # 표에 없는 세션 → 409 not_registered(남의 토큰 403 과 구별)
    stranger = secrets.token_hex(16)
    status, body = srv.req(
        "POST",
        "/worker/heartbeat",
        token="build-02",
        json_body={"worker_session_id": stranger, "seq": 1, "claims_settled": 0, "jobs": []},
    )
    assert status == 409 and body["code"] == "not_registered", body
    # 다른 이름의 토큰이 남의 현재 세션 id 를 실으면 403, `heard` 가 오르지 않는다
    theirs = srv.session("build-02")
    before = srv.app.heard_at(theirs)
    srv.clock.advance(30)
    for status, body in (
        srv.req(
            "POST",
            "/worker/heartbeat",
            token="build-03",
            json_body={"worker_session_id": theirs, "seq": 1, "claims_settled": 0, "jobs": [jid]},
        ),
        srv.req(
            "POST",
            "/worker/register",
            token="build-03",
            json_body={
                "worker_session_id": theirs,
                "pool": "default",
                "lanes": 1,
                "version": __version__,
            },
        ),
        srv.req(
            "POST",
            f"/worker/jobs/{jid}/finish",
            token="build-03",
            json_body={"worker_session_id": theirs, "outcome": "succeeded", "exit_code": 0},
        ),
    ):
        assert status == 403 and set(body) == {"error"}, body
    assert srv.app.heard_at(theirs) == before
    assert job_of(srv, jid).state == RUNNING
    # 모양이 틀린 세션 헤더 · 본문은 400 이고 본문 키는 error 하나
    status, body = srv.req(
        "POST",
        f"/worker/jobs/{jid}/log",
        token="build-02",
        body=b"x\n",
        headers={"Content-Type": "application/octet-stream", "X-RCM-Worker-Session": "nope"},
    )
    assert status == 400 and set(body) == {"error"}, body
    status, body = srv.req(
        "POST",
        "/worker/heartbeat",
        token="build-02",
        json_body={"worker_session_id": "XYZ", "seq": 1, "claims_settled": 0},
    )
    assert status == 400 and set(body) == {"error"}


def test_gap8_register_says_how_many_live_processes_it_replaced(srv):
    """# GAP8 명세 §9: 같은 토큰의 둘째 프로세스가 서비스 워커를 펜싱하면 서비스는 종료 0 이라
    다시 뜨지 않는다 — 둘째 프로세스가 시작할 때 그것을 말한다. 등록 응답의 `fenced_sessions` 는
    이 등록이 펜싱한 다른 세션 가운데 서버가 `worker_timeout_seconds` 안에 들은 것의 수다. 제가
    죽었다고 넘긴 세션(`previous_sessions`)과 오래 조용한 세션은 세지 않는다."""
    assert srv.registered("build-02").get("fenced_sessions") == 0  # 처음
    assert srv.registered("build-02").get("fenced_sessions") == 0  # 같은 세션의 재등록
    srv.new_process("build-02")
    assert srv.registered("build-02").get("fenced_sessions") == 1  # 살아 있던 프로세스를 대신했다
    assert srv.restarted("build-02").get("fenced_sessions") == 0  # 죽었다고 넘긴 세션
    srv.clock.advance(TIMEOUT + 1)
    srv.new_process("build-02")
    assert srv.registered("build-02").get("fenced_sessions") == 0  # 오래 조용했다


@pytest.mark.parametrize(("fenced", "said"), [(0, False), (1, True), (3, True)])
def test_gap8_the_worker_logs_one_line_when_it_replaced_a_live_process(tmp_path, fenced, said):
    """# GAP8 워커는 `fenced_sessions` 가 0 보다 크면 한 줄을 쓴다 — 서비스 워커가 멈췄고 스스로
    다시 뜨지 않는다는 것과 되살리는 길(문서)."""

    class _Replacing(_Who):
        def register(self, **kw: Any) -> dict[str, Any]:
            return {**super().register(**kw), "fenced_sessions": fenced}

    logs: list[str] = []
    w = _worker(tmp_path, _Replacing())
    w.log = logs.append
    w.register()
    line = [m for m in logs if "a service worker with this token has stopped" in m]
    noun = "process" if fenced == 1 else "processes"
    expected = (
        [
            f"replaced {fenced} other {noun} of worker build-02 — a service worker with this"
            " token has stopped and will not start again by itself"
        ]
        if said
        else []
    )
    assert line == expected


def _server_restarted(srv: WorkerServer) -> None:
    """서버 재시작 흉내 — 세션을 들은 시각은 메모리에만 있다(결정 266). 바닥은 지금이다."""
    with srv.app._heard_lock:
        srv.app._heard.clear()
    srv.app.started_mono = srv.clock.mono()


def test_p62_2_fenced_sessions_is_null_while_a_restarted_server_cannot_know(srv):
    """# P6-2 #2 재시작한 서버는 그 전에 들은 세션을 모른다. 기동 뒤 `worker_timeout_seconds` 가
    지나기 전에 펜싱한 세션이 아직 안 들렸으면 살아 있었는지 모른다 — 0 이 아니라 null 이다.
    그 시간이 지나면 안 들린 세션은 조용했던 것이라 0 이다."""
    srv.registered("build-02")
    _server_restarted(srv)
    srv.new_process("build-02")
    assert srv.registered("build-02")["fenced_sessions"] is None  # 모름
    assert srv.registered("build-02")["fenced_sessions"] == 0  # 같은 세션의 재등록은 안다
    _server_restarted(srv)
    srv.clock.advance(TIMEOUT + 1)
    srv.new_process("build-02")
    assert srv.registered("build-02")["fenced_sessions"] == 0  # 기동 뒤 내내 조용했다
    srv.new_process("build-02")
    assert srv.registered("build-02")["fenced_sessions"] == 1  # 기동 뒤에 들었다


def test_p62_2_the_worker_says_it_cannot_tell_when_fenced_sessions_is_null(tmp_path):
    """# P6-2 #2 `fenced_sessions` 가 null 이면 서버가 모른다 — 워커는 「대신했다」고 단정하지
    않고 모른다고 한 줄 쓴다. 키가 없는 옛 서버에는 아무것도 쓰지 않는다."""

    class _Unknown(_Who):
        def register(self, **kw: Any) -> dict[str, Any]:
            return {**super().register(**kw), "fenced_sessions": None}

    logs: list[str] = []
    w = _worker(tmp_path, _Unknown())
    w.log = logs.append
    w.register()
    assert [m for m in logs if "worker build-02" in m] == [
        "the server restarted recently and cannot tell whether this replaced another process of"
        " worker build-02 — if a service worker with this token was running, it has stopped and"
        " will not start again by itself"
    ]
    old: list[str] = []
    w = _worker(tmp_path / "old", _Who())
    w.log = old.append
    w.register()
    assert [m for m in old if "worker build-02" in m and "replace" in m] == []


@pytest.mark.parametrize("key", ["worker_session_id", "previous_sessions", "unverified_sessions"])
def test_gap7_a_session_id_with_a_trailing_newline_is_400(srv, key):
    """# GAP7 세션 id 는 32자 소문자 hex **전부**다. `$` 는 끝 줄바꿈 앞에서도 맞으므로
    `<32 hex>\\n` 이 지나갔다 — 같은 세션의 다른 문자열이 표에 들어갈 수 있었다. 400 이고 본문
    키는 error 하나, 세션 행은 생기지 않는다."""
    bent = secrets.token_hex(16) + "\n"
    body: dict[str, Any] = {"pool": "default", "lanes": 1, "version": __version__}
    body["worker_session_id"] = bent if key == "worker_session_id" else secrets.token_hex(16)
    if key != "worker_session_id":
        body[key] = [bent]
    status, answer = srv.req("POST", "/worker/register", token="build-02", json_body=body)
    assert (status, set(answer)) == (400, {"error"}), answer
    count = srv.store._conn().execute("SELECT COUNT(*) FROM worker_sessions").fetchone()[0]
    assert count == 0


def test_rl11_two_processes_registering_at_once_leave_exactly_one_current_session(srv):
    sids = [secrets.token_hex(16) for _ in range(6)]
    out: list[int] = []

    def reg(sid: str) -> None:
        status, _ = srv.req(
            "POST",
            "/worker/register",
            token="build-02",
            json_body={
                "worker_session_id": sid,
                "pool": "default",
                "lanes": 1,
                "version": __version__,
            },
        )
        out.append(status)

    threads = [threading.Thread(target=reg, args=(s,)) for s in sids]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert out.count(200) == len(sids)
    current = (
        srv.store._conn()
        .execute(
            "SELECT id FROM worker_sessions WHERE worker_name='build-02' AND fenced_at IS NULL"
        )
        .fetchall()
    )
    assert len(current) == 1


# ── 같은 세션의 재등록 · 순서(T10 · RL12 · RL13) ─────────────────────────────


def test_rl12_reregistering_the_same_session_is_idempotent(srv):
    srv.registered("build-02", lanes=2)
    a = running(srv, lane=1)
    srv.registered("build-02", lanes=2)  # 다른 레인이 not_registered 409 를 받아 재등록
    assert job_of(srv, a).state == RUNNING
    rows = srv.store._conn().execute("SELECT fenced_at FROM worker_sessions").fetchall()
    assert [r["fenced_at"] for r in rows] == [None]


def test_rl12_two_registers_of_one_new_session_racing_both_succeed(srv, monkeypatch):
    """같은 새 세션 id 의 등록 둘이 겹친다(레인 둘이 동시에 처음 등록). 늦은 쪽은 세션 행 PK 충돌로
    500 이 아니라 그 세션의 재등록처럼 멱등 200 이다(P3 1라운드 LOW)."""
    real_open = srv.store.open_session
    other: list[Any] = []

    def the_other_lane_first(name: str, session_id: str, **kw: Any):
        if not other:
            other.append(real_open(name, session_id, **kw))  # 다른 레인의 등록이 먼저 커밋했다
        return real_open(name, session_id, **kw)

    monkeypatch.setattr(srv.store, "open_session", the_other_lane_first)
    status, body = srv.register("build-02", lanes=2)
    assert len(other) == 1
    assert status == 200, body
    rows = srv.store._conn().execute("SELECT id, fenced_at FROM worker_sessions").fetchall()
    assert [(r["id"], r["fenced_at"]) for r in rows] == [(srv.session("build-02"), None)]


def test_rl12_spec_does_not_create_a_session(srv):
    """`rcm worker --check` · `rcm doctor` 는 spec · health · whoami 만 부른다(F46)."""
    srv.registered("build-02")
    jid = running(srv)
    assert srv.req("GET", "/worker/spec", token="build-02")[0] == 200
    assert srv.store._conn().execute("SELECT count(*) FROM worker_sessions").fetchone()[0] == 1
    assert srv.heartbeat("build-02", jobs=[jid])[0] == 200


def test_rl13_a_version_mismatch_answers_first_with_no_side_effect(srv):
    srv.registered("build-02")
    jid = running(srv)
    srv.new_process("build-02")
    status, body = srv.register("build-02", version="0.0.1")
    assert status == 409 and body["server_version"] == __version__
    assert "code" not in body
    assert job_of(srv, jid).state == RUNNING
    assert srv.store._conn().execute("SELECT count(*) FROM worker_sessions").fetchone()[0] == 1


# ── 레거시 · 옛 워커(T11 · T12 · T17 · RL24 · RL47) ───────────────────────────


def _legacy_running(srv: WorkerServer, preset: str = "ok") -> int:
    """v26 이전에 claim 된 원격 잡 — 세션 · 얼린 값이 NULL 이다."""
    jid = srv.queued_job(preset=preset)
    srv.store._conn().execute(
        "UPDATE jobs SET state=?, worker_name='build-02', lane=1, started_at=? WHERE id=?",
        (RUNNING, srv.clock().timestamp(), jid),
    )
    return jid


def test_rl24_an_old_worker_finishes_its_legacy_job_but_cannot_claim_or_register(srv):
    srv.store.register_worker(
        "build-02", pool="default", lanes=1, host_name=None, version=__version__, now=srv.clock()
    )
    jid = _legacy_running(srv)
    srv.queued_job()
    for path, body in (
        ("/worker/claim", {"lane": 1}),
        ("/worker/register", {"pool": "default", "lanes": 1, "version": __version__}),
    ):
        status, out = srv.req("POST", path, token="build-02", json_body=body, legacy=True)
        assert status == 409 and out["code"] == "protocol_required", out
        assert out["error"] == PROTOCOL_TEXT and "not registered" not in out["error"]
        assert out["worker_protocol"] == 2
    # 레거시 busy 잡이 있는 동안 세션 없는 heartbeat · log · finish 는 받는다
    status, _ = srv.req(
        "POST", "/worker/heartbeat", token="build-02", json_body={"jobs": [jid]}, legacy=True
    )
    assert status == 200
    status, _ = srv.req(
        "POST",
        f"/worker/jobs/{jid}/log",
        token="build-02",
        body=b"hi\n",
        headers={"Content-Type": "application/octet-stream"},
        legacy=True,
    )
    assert status == 200
    status, out = srv.req(
        "POST",
        f"/worker/jobs/{jid}/finish",
        token="build-02",
        json_body={"outcome": "succeeded", "exit_code": 0},
        legacy=True,
    )
    assert status == 200, out
    assert job_of(srv, jid).state == SUCCEEDED
    status, out = srv.req(
        "POST", "/worker/heartbeat", token="build-02", json_body={"jobs": []}, legacy=True
    )
    assert status == 409 and out["code"] == "protocol_required"
    # claim 은 한 건도 나가지 않았다
    assert [j.state for j in srv.store.list_active() if j.preset == "ok"] == [QUEUED]


@pytest.mark.parametrize(("preset", "held"), [("ship", True), ("ok", False)])
def test_rl47_the_first_session_closes_legacy_jobs_and_holds_irreversible_ones(srv, preset, held):
    srv.store.register_worker(
        "build-02", pool="default", lanes=1, host_name=None, version=__version__, now=srv.clock()
    )
    jid = _legacy_running(srv, preset)
    srv.registered("build-02")
    j = job_of(srv, jid)
    assert j.state == LOST and j.summary_code == "worker_restarted_without_job"
    rows = hold_rows(srv)
    if held:
        assert [(h["job_id"], h["cause"], h["concurrency_group"]) for h in rows] == [
            (jid, "worker_restarted_without_job", "irreversible:ship")
        ]
    else:
        assert rows == []
        nxt = srv.queued_job()
        assert srv.claimed("build-02", 1) == nxt  # 409 없이 다음 잡


def test_rl47_legacy_and_session_heard_do_not_feed_each_other(srv):
    srv.store.register_worker(
        "build-02", pool="default", lanes=2, host_name=None, version=__version__, now=srv.clock()
    )
    jid = _legacy_running(srv)
    key = "legacy:build-02"
    srv.clock.advance(10)
    srv.req("POST", "/worker/heartbeat", token="build-02", json_body={"jobs": [jid]}, legacy=True)
    legacy_at = srv.app.heard_at(key)
    srv.clock.advance(10)
    srv.registered("build-02", lanes=2)  # 첫 등록이 레거시 잡을 닫는다
    assert srv.app.heard_at(key) == legacy_at
    assert srv.app.heard_at(srv.session("build-02")) > legacy_at


# ── T5 순번 · 끝난 claim(RL9 · RL50) ───────────────────────────────────────────


@pytest.mark.parametrize(("preset", "held"), [("ok", False), ("ship", True)])
def test_rl9_a_lost_claim_answer_closes_after_the_claim_settled_and_holds_if_irreversible(
    srv, preset, held
):
    srv.registered("build-02")
    jid = srv.queued_job(preset=preset)
    status, _ = srv.claim("build-02", 1)  # 서버는 확정 — 워커는 응답을 잃었다고 친다
    assert status == 200
    assert srv.heartbeat("build-02", jobs=[])[0] == 200
    j = job_of(srv, jid)
    assert j.state == LOST and j.summary_code == "worker_restarted_without_job"
    causes = [(h["job_id"], h["cause"]) for h in hold_rows(srv)]
    assert causes == ([(jid, "worker_restarted_without_job")] if held else [])


@pytest.mark.parametrize("answer_lost", [False, True])
def test_rl9_a_restarted_process_closes_its_dead_sessions_irreversible_job_without_a_hold(
    srv, answer_lost
):
    """RL9 「프로세스가 죽고 다시 시작했으면 이어받기 등록이 닫고(확인, 홀드 0)」. 새 프로세스가
    D 의 폴더 잠금을 쥐고 D 를 `previous_sessions` 로 넘겼다 — D 의 그룹이 없다는 확인이다.
    `answer_lost` 는 T5 변형: D 가 claim 응답을 잃고(그 잡을 모른 채) 죽었다."""
    srv.registered("build-02")
    jid = srv.queued_job(preset="ship")
    status, _ = srv.claim("build-02", 1)
    assert status == 200
    if not answer_lost:  # D 가 그 잡을 돌렸다
        assert srv.heartbeat("build-02", jobs=[jid])[0] == 200
    assert job_of(srv, jid).state == RUNNING
    srv.clock.advance(5)
    srv.restarted("build-02")  # 새 프로세스 — previous_sessions=[D]
    j = job_of(srv, jid)
    assert j.state == LOST and j.summary_code == "worker_restarted_without_job"
    assert hold_rows(srv) == []
    # 그룹(irreversible:ship · global)이 풀려 같은 그룹의 다음 잡이 곧 시작한다
    nxt = srv.queued_job(preset="ship")
    assert srv.claimed("build-02", 1) == nxt


def test_rl50_stale_or_unsettled_heartbeats_do_not_close_a_running_job(srv):
    srv.registered("build-02")
    jid = srv.queued_job(preset="ship")
    assert srv.claimed("build-02", 1) == jid
    claim_seq = job_of(srv, jid).claim_seq
    assert srv.heartbeat("build-02", jobs=[jid], seq=3, claims_settled=claim_seq)[0] == 200
    assert srv.heartbeat("build-02", jobs=[jid], seq=5, claims_settled=claim_seq)[0] == 200
    # 늦게 온 seq 4(claim 전에 찍은 목록) — 받아들인 seq 이하라 판정하지 않는다
    assert srv.heartbeat("build-02", jobs=[], seq=4, claims_settled=claim_seq)[0] == 200
    assert job_of(srv, jid).state == RUNNING
    # seq 는 새것이지만 claims_settled 가 그 claim 보다 작다
    assert srv.heartbeat("build-02", jobs=[], seq=6, claims_settled=claim_seq - 1)[0] == 200
    assert job_of(srv, jid).state == RUNNING
    assert hold_rows(srv) == []
    # 같은 그룹 둘째 잡은 claim 되지 않는다(합성 그룹 · global)
    srv.registered("build-03")
    srv.queued_job(preset="ship")
    assert srv.claim("build-03", 1)[0] == 204


@pytest.mark.parametrize("bad", [True, "1", -1, 2**53])
def test_rl50_seq_and_claims_settled_must_be_json_integers(srv, bad):
    srv.registered("build-02")
    status, body = srv.heartbeat("build-02", jobs=[], seq=bad)
    assert status == 400 and set(body) == {"error"}
    status, body = srv.req(
        "POST", "/worker/claim", token="build-02", json_body={"lane": 1, "claim_seq": bad}
    )
    assert status == 400 and set(body) == {"error"}


# ── 닫힌 잡 통보(RL35) ─────────────────────────────────────────────────────────


def test_rl35_heartbeat_reports_this_sessions_closed_jobs_with_had_cancel(srv):
    srv.registered("build-02", lanes=2)
    a = running(srv, lane=1)
    b = running(srv, lane=2)
    assert srv.cancel(b)[1]["state"] == CANCELLING
    srv.store.finish(a, LOST, now=srv.clock(), summary="x")
    srv.store.finish(b, LOST, now=srv.clock(), summary="x")
    status, body = srv.heartbeat("build-02", jobs=[a, b, 999])
    assert status == 200
    assert body["closed_jobs"] == [
        {"id": a, "state": LOST, "had_cancel": False},
        {"id": b, "state": LOST, "had_cancel": True},
    ]
    srv.registered("build-03")
    status, body = srv.heartbeat("build-03", jobs=[a, b])
    assert body["closed_jobs"] == []  # 다른 세션의 잡은 안 실림


def test_rl35_a_closed_job_missing_from_the_heartbeat_list_is_not_reported(srv):
    """RL35 「그 세션의 잡만, 목록에 없는 잡 · 다른 세션의 잡은 안 실림」 — 이 세션이 닫힌 잡을
    가졌어도 워커가 그 잡을 `jobs` 에 싣지 않으면 `closed_jobs` 에 없다. 실은 잡만 실린다."""
    srv.registered("build-02", lanes=2)
    a = running(srv, lane=1)
    b = running(srv, lane=2)
    srv.store.finish(a, LOST, now=srv.clock(), summary="x")
    srv.store.finish(b, LOST, now=srv.clock(), summary="x")
    status, body = srv.heartbeat("build-02", jobs=[b])
    assert status == 200
    assert body["closed_jobs"] == [{"id": b, "state": LOST, "had_cancel": False}]
    status, body = srv.heartbeat("build-02", jobs=[])
    assert status == 200 and body["closed_jobs"] == []


def test_rl11_an_unknown_session_gets_not_registered_on_log_phase_and_finish(srv):
    """RL11 「표에 없으면 409 `not_registered`」 — heartbeat 만이 아니라 log · phase · finish
    도. 남의 토큰 403 · 펜싱 409 와 구별되고 잡 상태 · `log.txt` · phase 에 한 바이트도 반영되지
    않는다."""
    srv.registered("build-02")
    jid = running(srv)
    assert srv.log("build-02", jid, b"::rcm::step::real\n")[0] == 200
    before_job = srv.view(jid, token="alice")
    before_sha = _log_sha(srv, jid)
    stranger = secrets.token_hex(16)
    for status, body in (
        srv.req(
            "POST",
            f"/worker/jobs/{jid}/log",
            token="build-02",
            body=b"::rcm::step::evil\n",
            headers={"Content-Type": "application/octet-stream", "X-RCM-Worker-Session": stranger},
        ),
        srv.req(
            "POST",
            f"/worker/jobs/{jid}/phase",
            token="build-02",
            json_body={"worker_session_id": stranger, "phase": "executing"},
        ),
        srv.req(
            "POST",
            f"/worker/jobs/{jid}/finish",
            token="build-02",
            json_body={"worker_session_id": stranger, "outcome": "succeeded", "exit_code": 0},
        ),
    ):
        assert status == 409 and body["code"] == "not_registered", (status, body)
    assert _log_sha(srv, jid) == before_sha
    after_job = srv.view(jid, token="alice")
    for key in ("state", "phase", "last_step", "exit_code", "summary"):
        assert after_job.get(key) == before_job.get(key), key
    assert job_of(srv, jid).state == RUNNING


# ── 홀드 풀기 · 인증된 확인(RL44) ─────────────────────────────────────────────


def test_rl44_previous_sessions_release_a_hold_and_a_foreign_name_cannot(srv):
    srv.registered("build-02")
    jid = running(srv, preset="ship")
    srv.store.finish(jid, LOST, now=srv.clock(), summary="x", hold_cause="worker_unreachable")
    assert len(hold_rows(srv)) == 1
    old = srv.session("build-02")
    # (d) 다른 이름의 워커 토큰이 그 세션 id 를 previous_sessions 로 보낸다 → 403, 홀드 그대로
    srv.registered("build-03")
    srv.new_process("build-03")
    status, body = srv.register("build-03", previous_sessions=[old])
    assert status == 403 and set(body) == {"error"}
    assert hold_rows(srv)[0]["released_at"] is None
    # (a) 같은 이름의 새 프로세스가 D 의 그룹이 없음을 확인 → 풀림, 대기 잡이 시작
    waiting = srv.queued_job(preset="ship")
    srv.restarted("build-02")
    (h,) = hold_rows(srv)
    assert h["release_kind"] == "worker" and h["released_by"] == "build-02"
    assert srv.claimed("build-02", 1) == waiting


def test_rl44_a_late_finish_with_group_gone_releases_the_hold(srv):
    srv.registered("build-02")
    jid = running(srv, preset="ship")
    srv.store.finish(jid, LOST, now=srv.clock(), summary="x", hold_cause="worker_unreachable")
    status, body = srv.finish("build-02", jid, "lost")  # group_gone 없음 — 그대로
    assert status == 409 and body["code"] == "job_closed" and body["hold_released"] is False
    status, body = srv.req(
        "POST",
        f"/worker/jobs/{jid}/finish",
        token="build-02",
        json_body={"outcome": "lost", "exit_code": None, "group_gone": True},
    )
    assert status == 409 and body["hold_released"] is True and body["state"] == LOST
    assert hold_rows(srv)[0]["release_kind"] == "worker"


def test_rl44_a_b_late_finish_after_an_unconfirmed_cancel_releases_the_hold_only_with_group_gone(
    srv,
):
    """확인 없이 `cancelled` 로 닫힌 표식 잡(취소가 워커에 닿지 않았다)의 홀드도 같은 세션의 늦은
    finish 가 `group_gone: true` 일 때만 푼다(§4.12 표의 1). `lost` 로 닫힌 잡은 M5t-b PR2 부터
    `late_result` 트랜잭션(`record_late_result`)에서 풀므로 rl44_a 는 이 갈래에 닿지 않는다 — 이
    시험이 `lost` 아닌 닫힘의 갈래를 본다."""
    srv.registered("build-02")
    jid = running(srv, preset="ship")
    assert srv.store.finish(
        jid, "cancelled", now=srv.clock(), summary="x", hold_cause="cancel_unconfirmed"
    )
    status, body = srv.finish("build-02", jid, "lost")  # group_gone 없음 — 그대로
    assert status == 409 and body["code"] == "job_closed" and body["hold_released"] is False
    assert hold_rows(srv)[0]["release_kind"] is None
    status, body = srv.req(
        "POST",
        f"/worker/jobs/{jid}/finish",
        token="build-02",
        json_body={"outcome": "lost", "exit_code": None, "group_gone": True},
    )
    assert status == 409 and body["hold_released"] is True and body["state"] == "cancelled"
    assert hold_rows(srv)[0]["release_kind"] == "worker"


def test_rl44_a_live_irreversible_finish_without_group_gone_holds(srv):
    srv.registered("build-02")
    jid = running(srv, preset="ship")
    assert srv.finish("build-02", jid, "succeeded", exit_code=0)[0] == 200
    assert [h["cause"] for h in hold_rows(srv)] == ["group_not_gone"]
    nxt = srv.queued_job(preset="ship")
    assert srv.claim("build-02", 1)[0] == 204  # 그 그룹은 막혔다
    assert job_of(srv, nxt).state == QUEUED


# ── 워커 쪽: 등록 전 프로토콜 확인 · 종료 코드(T13 · W5 · W6 · RL25 · RL32) ─────


class _OldServer:
    """프로토콜 키가 없는 옛 서버의 워커 클라이언트 흉내. 부른 것을 적는다."""

    def __init__(self, version: str, *, spec_status: int = 200):
        self.server = "http://old"
        self.token = "t"
        self.calls: list[str] = []
        self.version = version
        self.spec_status = spec_status

    def spec(self) -> dict[str, Any]:
        self.calls.append("GET /worker/spec")
        if self.spec_status != 200:
            raise ClientError(self.spec_status, "not found")
        return {"generation": "1:0", "presets": []}

    def health(self) -> dict[str, Any]:
        self.calls.append("GET /api/health")
        return {"version": self.version}

    def register(self, **kw: Any) -> dict[str, Any]:
        self.calls.append("POST /worker/register")
        raise ClientError(409, "worker version x, server y", {"server_version": self.version})


@pytest.mark.parametrize("spec_status", [200, 404])
def test_rl25_a_new_worker_against_a_protocol_1_server_exits_3_before_registering(
    tmp_path, spec_status
):
    fake = _OldServer(__version__, spec_status=spec_status)
    cfg = WorkerConfig(server="http://old", token="t", data_dir=str(tmp_path / "w"))
    for _ in range(3):  # 감독자가 되살려도 같다
        w = RemoteWorker(cfg, client=fake, log=lambda m: None)
        with pytest.raises(WorkerExit) as e:
            w.run()
        assert e.value.code == EXIT_PROTOCOL
        assert "protocol" in e.value.message
    assert "POST /worker/register" not in fake.calls
    assert set(fake.calls) <= {"GET /worker/spec", "GET /api/health"}
    assert not (tmp_path / "w" / "worker-sessions").exists()


def test_rl32_a_different_version_goes_the_register_path_and_follows(tmp_path):
    fake = _OldServer("9.9.9")
    cfg = WorkerConfig(server="http://old", token="t", data_dir=str(tmp_path / "w"))
    w = RemoteWorker(cfg, client=fake, log=lambda m: None)
    with pytest.raises(WorkerExit) as e:
        w.run()
    assert e.value.code == 2  # WorkerFollow(따라가기) 또는 종료 2 — 종료 3 이 아니다
    assert fake.calls[-1] == "POST /worker/register"


@pytest.fixture
def follow_env(monkeypatch, capsys, tmp_path):
    """`rcm worker` 한 대와 프로토콜 2 의 진짜 서버(`test_cli_worker_m5q.WorkerEnv`)."""
    from test_cli_worker_m5q import WorkerEnv

    env = WorkerEnv(monkeypatch, capsys, tmp_path)
    yield env
    env.close()


@pytest.mark.skipif(os.name != "posix", reason="follow is POSIX only (결정 172)")
@pytest.mark.parametrize("following", [True, False])
def test_rl32_a_version_409_at_register_follows_or_exits_2_and_leaves_no_session(
    follow_env, following
):
    """(켬) 등록 직전에 서버 버전이 바뀌면 프로토콜 확인은 종료 3 으로 끝내지 않고 오늘의 등록
    길로 가서 버전 409 를 받아 따라간다(M5q ②). (끔) 같은 409 로 종료 2. 어느 쪽이든 서버에
    `workers` 행도 세션 행도 생기지 않는다."""
    from follow_helpers import INSTALLED, NEW
    from test_cli_worker import WORKER

    env = follow_env
    env.bump_before_register(NEW)
    r = env.run(env.argv(), enabled=following)
    assert r.code == ("exec" if following else 2), r.err
    assert env.registers == [INSTALLED]  # 등록 길을 한 번 갔다
    assert "protocol" not in r.err
    assert env.srv.store.get_worker(WORKER) is None
    count = env.srv.store._conn().execute(
        "SELECT COUNT(*) FROM worker_sessions WHERE worker_name=?", (WORKER,)
    )
    assert count.fetchone()[0] == 0
    if following:
        assert env.execs[-1][2]["RCM_FOLLOWED"] == NEW


def test_w5_a_fenced_worker_process_stops_and_exits_zero(srv, tmp_path):
    """W5: 살아 있는 프로세스의 요청이 409 session_fenced 면 모든 레인을 멈추고 종료 0."""
    url = f"http://127.0.0.1:{srv.port}"
    cfg = WorkerConfig(
        server=url, token=srv.tokens["build-02"], data_dir=str(tmp_path / "w"), lanes=1
    )
    w = RemoteWorker(cfg, log=lambda m: None)
    register = w.register

    def short_polls() -> dict[str, Any]:
        # 레인의 long-poll 을 1초로 — 펜싱 뒤 레인이 20초 claim 을 다 기다리지 않게(시험 시간만)
        answer = register()
        w.claim_wait_seconds = 1
        return answer

    w.register = short_polls  # type: ignore[method-assign]
    out: dict[str, Any] = {}
    t = threading.Thread(target=lambda: out.update(code=w.run()), daemon=True)
    t.start()
    for _ in range(200):
        if srv.store.get_session(w.session_id) is not None:
            break
        threading.Event().wait(0.05)
    srv.new_process("build-02")
    srv.registered("build-02")  # 같은 토큰의 새 프로세스가 등록 → w 는 펜싱됐다
    w.heartbeat_once()
    t.join(30)
    assert not t.is_alive()
    assert out["code"] == 0 and w.fenced.is_set()


def test_rl10_a_fake_supervisor_does_not_bring_back_a_fenced_process(srv, tmp_path):
    """가짜 감독자(launchd `KeepAlive.SuccessfulExit=false` · systemd `Restart=on-failure` 흉내 —
    0 이 아닌 종료면 새 프로세스를 띄운다) 아래의 A 를 같은 토큰의 B 가 펜싱한다. A 는 종료 0 이라
    되살아나지 않는다 — 1분 안 `register` 는 A 하나 · B 하나로 2번이고 B 가 현재 세션으로 남는다."""
    url = f"http://127.0.0.1:{srv.port}"
    registers: list[str] = []
    real = srv.app.worker_register

    def counting(token: Any, body: Any) -> dict[str, Any]:
        if token.name == "build-02":
            registers.append(str((body or {}).get("worker_session_id")))
        return real(token, body)

    srv.app.worker_register = counting  # type: ignore[method-assign]
    procs: list[RemoteWorker] = []
    codes: list[int | None] = []

    def supervisor() -> None:
        for _ in range(4):  # 핑퐁이면 여기서 되살린다
            cfg = WorkerConfig(
                server=url, token=srv.tokens["build-02"], data_dir=str(tmp_path / "w"), lanes=1
            )
            w = RemoteWorker(cfg, log=lambda m: None)
            register = w.register

            def short_polls(w: RemoteWorker = w, register: Any = register) -> dict[str, Any]:
                answer = register()
                w.claim_wait_seconds = 1  # 시험 시간만 — 레인이 20초 claim 을 다 기다리지 않게
                return answer

            w.register = short_polls  # type: ignore[method-assign]
            procs.append(w)
            try:
                code: int | None = w.run()
            except WorkerExit as e:
                code = e.code
            codes.append(code)
            if code == 0:
                return

    started = time.monotonic()
    t = threading.Thread(target=supervisor, daemon=True)
    t.start()
    for _ in range(200):
        if procs and srv.store.get_session(procs[0].session_id) is not None:
            break
        threading.Event().wait(0.05)
    srv.new_process("build-02")
    srv.registered("build-02")  # B — A 를 펜싱한다
    b = srv.session("build-02")
    procs[0].heartbeat_once()  # A 의 다음 요청이 409 session_fenced
    while t.is_alive() and len(registers) <= 2 and time.monotonic() - started < 40:
        threading.Event().wait(0.05)
    threading.Event().wait(1.0)  # 감독자가 되살릴 틈
    assert time.monotonic() - started < 60
    assert len(registers) <= 2 and len(procs) == 1, registers
    assert codes == [0] and not t.is_alive()
    assert srv.store.current_session("build-02") == b  # B 가 되펜싱되지 않았다


def test_rl10_an_unconfirmed_fenced_process_keeps_the_job_until_its_last_heard_plus_g(srv):
    """A 가 분단돼 확인을 못 보내면 X 는 A 의 마지막 들음 + G 까지 busy, 그 뒤 `lost`."""
    srv.registered("build-02")
    jid = running(srv)
    grace = disconnect_grace(srv.cfg.server)
    srv.clock.advance(5)
    assert srv.heartbeat("build-02", jobs=[jid])[0] == 200  # A 의 마지막 들음
    srv.new_process("build-02")
    srv.registered("build-02")  # B 가 A 를 펜싱한다 — A 는 분단돼 모른다
    for _ in range(int(grace // 30)):
        srv.clock.advance(30)
        assert srv.heartbeat("build-02", jobs=[])[0] == 200  # B 는 살아 있다
        assert srv.app.mark_lost_workers(srv.clock.now) == []
        assert job_of(srv, jid).state == RUNNING  # 확인 전까지 그룹 자리가 안 풀린다
    srv.clock.advance(grace - (grace // 30) * 30 + 1)
    assert srv.app.mark_lost_workers(srv.clock.now) == [jid]
    j = job_of(srv, jid)
    assert j.state == LOST and j.summary_code == "worker_unreachable"


def test_parse_preset_accepts_the_irreversible_key_used_here():
    assert parse_preset(sh("ship", "echo ship", irreversible=True)).irreversible is True


# ── 세션 폴더 · 고아 정리(§4.4 · RL14 b · RL15 · RL31 · RL38) ─────────────────


def _spawn_group(argv: list[str]) -> subprocess.Popen:
    p = subprocess.Popen(argv, start_new_session=True, stdout=subprocess.DEVNULL)
    # 진짜 고아는 init 이 거둔다 — 시험의 자식이 좀비로 남아 그룹을 붙들지 않게 곧바로 거둔다
    threading.Thread(target=p.wait, daemon=True).start()
    return p


def _kill(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _dead_job(tmp_path: Path, proc: dict[str, Any] | None, job_id: int = 5) -> Path:
    d = tmp_path / "jobs" / str(job_id)
    d.mkdir(parents=True)
    if proc is not None:
        (d / "proc.json").write_text(json.dumps(proc), encoding="utf-8")
    return d


def _gone(p: subprocess.Popen, seconds: float = 5.0) -> bool:
    for _ in range(int(seconds / 0.02)):
        if p.poll() is not None:
            return True
        time.sleep(0.02)
    return False


def test_rl15_linux_start_reads_field_22_after_the_last_parenthesis(tmp_path):
    root = tmp_path / "proc"
    (root / "42").mkdir(parents=True)
    fields = " ".join(["S"] + [str(n) for n in range(4, 22)] + ["987654"] + ["0"] * 30)
    (root / "42" / "stat").write_text(f"42 (we) ird (name)) {fields}\n", encoding="ascii")
    assert rw.linux_start(42, root) == "linux:987654"
    assert rw.linux_start(43, root) is None


def test_rl15_ps_is_read_in_the_c_locale_and_utc_and_a_locale_answer_is_unknown(monkeypatch):
    seen: dict[str, Any] = {}

    def fake_run(argv, **kw):
        seen.update(kw["env"])
        return subprocess.CompletedProcess(argv, 0, stdout=seen["answer"], stderr="")

    monkeypatch.setattr(rw.subprocess, "run", fake_run)
    seen["answer"] = " Wed Nov 25 18:32:46 2026\n"
    assert rw.ps_start(1) == "ps:Wed Nov 25 18:32:46 2026"
    assert seen.get("LC_ALL") == "C" and seen.get("TZ") == "UTC"
    seen["answer"] = "2026년 11월 26일 목요일 03시 32분 46초\n"
    assert rw.ps_start(1) is None


def test_rl15_the_same_group_is_stopped_and_confirmed(tmp_path):
    p = _spawn_group(["sleep", "30"])
    try:
        d = _dead_job(tmp_path, {"pgid": p.pid, "start": rw.process_start(p.pid)})
        logs: list[str] = []
        assert rw.judge_orphan(d, 1.0, logs.append) == rw.CONFIRMED
        assert _gone(p)
        assert any("stopped process group" in m for m in logs)
    finally:
        _kill(p.pid)


def test_rl15_a_reused_pid_is_left_alone_and_confirmed(tmp_path):
    p = _spawn_group(["sleep", "30"])
    try:
        other = "ps:Mon Jan  1 00:00:00 2001" if sys.platform != "linux" else "linux:1"
        d = _dead_job(tmp_path, {"pgid": p.pid, "start": other})
        assert rw.judge_orphan(d, 1.0, lambda m: None) == rw.CONFIRMED
        assert p.poll() is None  # 무관한 프로세스가 산다
    finally:
        _kill(p.pid)


def test_rl31_identity_unknown_cases_send_no_signal(tmp_path, monkeypatch):
    # (c) pgid null — 띄우는 순간 죽었을 수 있다
    assert rw.judge_orphan(_dead_job(tmp_path, {"pgid": None, "start": None}, 1), 1, print) == (
        rw.UNKNOWN
    )
    # proc.json 이 없다 — 띄운 적 없음(결정 333)
    assert rw.judge_orphan(_dead_job(tmp_path, None, 2), 1, print) == rw.CONFIRMED
    # (a) 신원을 못 읽는다
    p = _spawn_group(["sleep", "30"])
    try:
        d = _dead_job(tmp_path, {"pgid": p.pid, "start": rw.process_start(p.pid)}, 3)
        monkeypatch.setattr(rw, "process_start", lambda pid: None)
        assert rw.judge_orphan(d, 1, lambda m: None) == rw.UNKNOWN
        assert p.poll() is None
    finally:
        _kill(p.pid)


@pytest.mark.parametrize("broken", ["unreadable", "not_json", "not_an_object"])
def test_rl31_a_proc_record_that_exists_but_cannot_be_read_is_unknown(tmp_path, broken):
    """`proc.json` 이 **없으면** 띄운 적 없음(CONFIRMED)이다. 있는데 읽지 못하거나 깨졌으면
    띄웠는지 모른다 — UNKNOWN 이고 신호도 없다(P3 2라운드 LOW). 없음과 못 읽음을 섞으면 도는
    그룹이 사라졌다고 판정받는다."""
    d = _dead_job(tmp_path, None)
    path = d / "proc.json"
    if broken == "unreadable":
        path.mkdir()  # 읽으면 OSError(IsADirectoryError)
    elif broken == "not_json":
        path.write_text('{"pgid": 4', encoding="utf-8")
    else:
        path.write_text("[1, 2]", encoding="utf-8")
    logs: list[str] = []
    assert rw.judge_orphan(d, 1, logs.append) == rw.UNKNOWN
    assert any("proc.json" in m for m in logs)


def test_rl31_a_leaderless_group_is_unknown_and_left_alone(tmp_path):
    """(b) 리더(pid = pgid)가 죽고 멤버만 산다(F59 · W13)."""
    pidfile = tmp_path / "member.pid"
    code = (
        "import os, time\n"
        "if os.fork() == 0:\n"
        f"    open({str(pidfile)!r}, 'w').write(str(os.getpid())); time.sleep(30); os._exit(0)\n"
        f"while not os.path.exists({str(pidfile)!r}): time.sleep(0.02)\n"
    )
    leader = _spawn_group([sys.executable, "-c", code])
    leader.wait(10)
    member = int(pidfile.read_text())
    try:
        d = _dead_job(tmp_path, {"pgid": leader.pid, "start": "ps:Mon Jan  1 00:00:00 2001"})
        assert rw.judge_orphan(d, 1, lambda m: None) == rw.UNKNOWN
        os.kill(member, 0)  # 아직 산다
    finally:
        _kill(leader.pid)


def _worker(tmp_path: Path, fake: Any, **cfg: Any) -> RemoteWorker:
    config = WorkerConfig(
        server="http://srv", token="t", data_dir=str(tmp_path / "w"), lanes=1, **cfg
    )
    w = RemoteWorker(config, client=fake, log=lambda m: None)
    w.config.data_path.mkdir(parents=True, exist_ok=True)
    return w


class _Who:
    """whoami · register · finish 만 하는 대역."""

    server = "http://srv"

    def __init__(self, name: str = "build-02") -> None:
        self.name = name
        self.registered: list[dict[str, Any]] = []
        self.finished: list[tuple[str, int, dict[str, Any]]] = []
        self.session_id = ""

    def whoami(self) -> dict[str, Any]:
        return {"name": self.name, "kind": "worker", "admin": False}

    def register(self, **kw: Any) -> dict[str, Any]:
        self.registered.append(kw)
        return {"name": self.name, "heartbeat_seconds": 5, "worker_timeout_seconds": 60}

    def finish(self, job_id: int, outcome: str, rc: Any, summary: Any, **extra: Any) -> dict:
        self.finished.append((self.session_id, job_id, {"outcome": outcome, **extra}))
        return {}


def _dead_folder(w: RemoteWorker, owner: dict[str, str] | None, jobs: dict[int, Any]) -> Path:
    sid = secrets.token_hex(16)
    d = w.config.data_path / rw.SESSIONS_DIR / sid
    d.mkdir(parents=True)
    (d / "lock").write_text("", encoding="utf-8")
    if owner is not None:
        (d / "owner.json").write_text(json.dumps(owner), encoding="utf-8")
    for jid, proc in jobs.items():
        j = d / "jobs" / str(jid)
        j.mkdir(parents=True)
        (j / "claim.json").write_text(
            json.dumps({"irreversible": True, "disconnect_grace_seconds": 600}), encoding="utf-8"
        )
        if proc is not None:
            (j / "proc.json").write_text(json.dumps(proc), encoding="utf-8")
    return d


def test_rl14_dead_sessions_go_to_previous_or_unverified_in_the_first_register(tmp_path):
    fake = _Who()
    w = _worker(tmp_path, fake)
    owner = rw.owner_of(w.config, "build-02")
    gone = _dead_folder(w, owner, {3: None})  # 띄운 적 없음 → 확인됨
    unsure = _dead_folder(w, owner, {4: {"pgid": None, "start": None}})  # 모름
    w.prepare_sessions()
    w.register()
    assert fake.registered[0].get("previous_sessions") == [gone.name]
    assert fake.registered[0]["unverified_sessions"] == [unsure.name]
    w.register()  # 다시 등록할 때는 싣지 않는다
    assert "previous_sessions" not in fake.registered[1]
    own = w.session_dir
    assert own is not None and own.name == w.session_id
    assert json.loads((own / "owner.json").read_text()) == owner
    assert "t" not in json.dumps(owner).replace("token_name", "")  # 토큰 값은 안 적는다


class _RegisterFails(_Who):
    """프로토콜 2 서버인데 등록만 5xx 다 — 재시작 루프의 한 번(P6 GAP1)."""

    def spec(self) -> dict[str, Any]:
        return {"worker_protocol": 2}

    def register(self, **kw: Any) -> dict[str, Any]:
        self.registered.append(kw)
        raise ClientError(500, "internal error")


def test_gap1_seventy_failed_starts_leave_no_folder_and_the_next_register_succeeds(srv, tmp_path):
    """# GAP1 등록이 실패한 시작(5xx · 403 · 버전 불일치)은 자기 세션 폴더를 남기지 않는다. 남기면
    다음 시작마다 잡 없는 죽은 세션이 `previous_sessions` 에 하나씩 붙어 65번째에 서버의 64 상한
    400 을 받고 — register 는 모든 HTTP 상태를 종료 2 로 읽는다 — 서버가 멀쩡해져도 영영 등록하지
    못한다."""
    url = f"http://127.0.0.1:{srv.port}"
    cfg = WorkerConfig(
        server=url, token=srv.tokens["build-02"], data_dir=str(tmp_path / "w"), lanes=1
    )
    for _ in range(70):
        w = RemoteWorker(cfg, client=_RegisterFails(), log=lambda m: None)
        with pytest.raises(WorkerExit) as e:
            w.run()
        assert e.value.code == 2
    base = tmp_path / "w" / rw.SESSIONS_DIR
    assert sorted(p.name for p in base.iterdir()) == []
    w = RemoteWorker(cfg, log=lambda m: None)
    w.prepare_sessions()
    try:
        w.register()
        code = None
    except WorkerExit as e:
        code = e.code
    assert code is None
    assert srv.store.get_session(w.session_id) is not None


def test_p62_4_a_dead_session_the_server_never_knew_is_ignored_and_its_folder_removed(
    srv, tmp_path
):
    """# P6-2 #4 `previous_sessions` 에 서버가 모르는 세션 id 가 들어갈 수 있다(등록 응답을 못
    받고 죽은 프로세스, 다른 서버를 보던 폴더). 서버는 그 id 를 무시하고 등록은 성공한다 —
    세션 행을 만들지 않고 남의 이름으로 막지도 않는다. 워커는 보고한 폴더를 지운다."""
    url = f"http://127.0.0.1:{srv.port}"
    cfg = WorkerConfig(
        server=url, token=srv.tokens["build-02"], data_dir=str(tmp_path / "w"), lanes=1
    )
    w = RemoteWorker(cfg, log=lambda m: None)
    w.config.data_path.mkdir(parents=True, exist_ok=True)
    owner = rw.owner_of(w.config, "build-02")
    unknown = _dead_folder(w, owner, {3: None})  # 띄운 적 없는 잡 하나 — 확인된 죽은 세션
    assert srv.store.get_session(unknown.name) is None
    w.prepare_sessions()
    assert w.previous_sessions == [unknown.name]
    out = w.register()
    assert out["name"] == "build-02"
    assert srv.store.get_session(unknown.name) is None  # 서버는 그 id 를 표에 넣지 않았다
    assert srv.store.get_session(w.session_id) is not None
    assert not unknown.exists()  # 보고했으니 폴더를 지웠다
    assert out["fenced_sessions"] == 0  # 모르는 id 는 대신한 프로세스로도 세지 않는다


def _aged(d: Path, at: float) -> Path:
    os.utime(d / "lock", (at, at))  # `ok_at.json` 이 없으면 D 의 나이는 `lock` 의 mtime 이다
    return d


def test_gap1_more_than_64_dead_sessions_go_oldest_first_and_the_rest_wait_on_disk(tmp_path):
    """# GAP1 죽은 세션이 서버의 목록 상한(64)보다 많으면 오래된 것부터 64개만 싣고 나머지는 지우지
    않고 다음 시작에 싣는다. 등록이 받은 확인된 세션의 폴더는 그 자리에서 지운다 — 다시 보고할
    것이 없다(잡이 모두 「확인됨」이고 서버가 닫고 펜싱했다). 「모름」 세션은 재확인에 쓰여
    남는다."""
    fake = _Who()
    w = _worker(tmp_path, fake)
    owner = rw.owner_of(w.config, "build-02")
    start = time.time() - 3600
    dead = [_aged(_dead_folder(w, owner, {}), start + i) for i in range(70)]
    unsure = [
        _aged(_dead_folder(w, owner, {4: {"pgid": None, "start": None}}), start + i)
        for i in range(66)
    ]
    w.prepare_sessions()
    w.register()
    assert fake.registered[0]["previous_sessions"] == [d.name for d in dead[:64]]
    assert fake.registered[0]["unverified_sessions"] == [d.name for d in unsure[:64]]
    assert [d.exists() for d in dead] == [False] * 64 + [True] * 6
    assert all(d.exists() for d in unsure)
    os.close(w._session_fd)  # 이 프로세스가 죽었다 — 제 폴더는 다음 프로세스의 죽은 세션이다
    for d in w.dead_sessions:
        if d.session_id in {u.name for u in unsure}:
            os.close(d.fd)
    fake2 = _Who()
    w2 = _worker(tmp_path, fake2)
    w2.prepare_sessions()
    w2.register()
    assert fake2.registered[0]["previous_sessions"][:6] == [d.name for d in dead[64:]]
    assert not any(d.exists() for d in dead)


def test_rl38_a_foreign_or_ownerless_folder_is_not_locked_judged_or_touched(tmp_path):
    p = _spawn_group(["sleep", "30"])
    try:
        fake = _Who()
        w = _worker(tmp_path, fake)
        proc = {"pgid": p.pid, "start": rw.process_start(p.pid)}
        other = rw.owner_of(
            WorkerConfig(server="http://other", token="x", data_dir=str(tmp_path)), "tester"
        )
        foreign = _dead_folder(w, other, {9: proc})
        ownerless = _dead_folder(w, None, {10: proc})
        before = {
            f: f.read_bytes() for d in (foreign, ownerless) for f in d.rglob("*") if f.is_file()
        }
        w.prepare_sessions()
        assert p.poll() is None  # 남의 고아를 죽이지 않는다
        assert w.previous_sessions == [] and w.unverified_sessions == []
        after = {
            f: f.read_bytes() for d in (foreign, ownerless) for f in d.rglob("*") if f.is_file()
        }
        assert after == before
    finally:
        _kill(p.pid)


def test_rl38_the_sweep_deletes_only_old_and_lockable_folders(tmp_path):
    fake = _Who()
    w = _worker(tmp_path, fake)
    old = _dead_folder(w, None, {})
    held = _dead_folder(w, None, {})
    fresh = _dead_folder(w, None, {})
    week = time.time() - 8 * 86400
    for d in (old, held):
        os.utime(d, (week, week))
    fd = rw.try_lock(held / "lock")
    try:
        rw.sweep_sessions(w.config.data_path)
        assert not old.exists() and held.exists() and fresh.exists()
    finally:
        assert fd is not None
        os.close(fd)


def test_rl15_the_new_process_stops_the_orphan_before_its_first_claim(srv, tmp_path):
    """같은 데이터 폴더로 새 프로세스를 띄우면 claim 을 시작하기 **전에** 죽은 세션의 표식 잡
    그룹을 정리한다 — 첫 claim 이 서버에 닿는 순간 고아 pid 는 이미 없고, 정리 결과가 워커 로그에
    남는다. 등록은 그 세션을 `previous_sessions` 로 싣는다."""
    url = f"http://127.0.0.1:{srv.port}"
    cfg = WorkerConfig(
        server=url,
        token=srv.tokens["build-02"],
        data_dir=str(tmp_path / "w"),
        lanes=1,
        grace_seconds=1,
    )
    logs: list[str] = []
    w = RemoteWorker(cfg, log=logs.append)
    w.config.data_path.mkdir(parents=True, exist_ok=True)
    worker_register = w.register

    def short_polls() -> dict[str, Any]:
        answer = worker_register()
        w.claim_wait_seconds = 1  # 시험 시간만 — 멈춘 뒤 레인이 20초 claim 을 다 기다리지 않게
        return answer

    w.register = short_polls  # type: ignore[method-assign]
    grp = _spawn_group(["sleep", "30"])
    seen: dict[str, Any] = {}
    real_claim = srv.app.worker_claim
    real_register = srv.app.worker_register

    def register(token: Any, body: Any) -> dict[str, Any]:
        seen.setdefault("previous", list((body or {}).get("previous_sessions") or []))
        return real_register(token, body)

    def claim(token: Any, body: Any, *a: Any, **kw: Any) -> Any:
        if "alive_at_first_claim" not in seen:
            seen["alive_at_first_claim"] = grp.poll() is None
            w.stop()
        return real_claim(token, body, *a, **kw)

    srv.app.worker_register = register  # type: ignore[method-assign]
    srv.app.worker_claim = claim  # type: ignore[method-assign]
    try:
        start = rw.process_start(grp.pid)
        d = _dead_folder(
            w, rw.owner_of(w.config, "build-02"), {6: {"pgid": grp.pid, "start": start}}
        )
        out: dict[str, Any] = {}
        t = threading.Thread(target=lambda: out.update(code=w.run()), daemon=True)
        t.start()
        t.join(40)
        assert not t.is_alive()
        assert seen.get("alive_at_first_claim") is False  # 첫 claim 전에 정리됐다
        assert seen["previous"] == [d.name]
        assert any(m.startswith(f"orphan #6: stopped process group {grp.pid}") for m in logs)
    finally:
        _kill(grp.pid)


def test_rl31_the_recheck_confirms_and_sends_one_narrow_finish_as_the_dead_session(tmp_path):
    fake = _Who()
    w = _worker(tmp_path, fake)
    owner = rw.owner_of(w.config, "build-02")
    d = _dead_folder(w, owner, {4: {"pgid": None, "start": None}})
    w.prepare_sessions()
    (dead,) = w.dead_sessions
    assert w.recheck(dead, 4) == rw.UNKNOWN and fake.finished == []  # 그래도 모름 — 안 보냄
    (d / "jobs" / "4" / "proc.json").unlink()  # 이제 확인됨(띄운 적 없음)
    assert w.recheck(dead, 4) == rw.CONFIRMED
    (sent,) = fake.finished
    assert sent[0] == d.name and sent[1] == 4  # D 의 세션 id 로
    assert sent[2] == {
        "outcome": LOST,
        "summary_code": "worker_replaced",
        "summary_args": {},
        "group_gone": True,
    }
    assert fake.session_id == w.session_id  # 살아 있는 세션의 클라이언트는 그대로


def test_rl31_unknown_identity_warns_then_the_recheck_stops_the_same_group(tmp_path, monkeypatch):
    """(a) 신원을 못 읽는 살아 있는 그룹: 새 프로세스는 그 그룹을 죽이지 않고 워커 로그에 경고를
    남기며 D 를 `unverified_sessions` 에 싣는다. 재확인 때 (가) 신원이 읽히고 같은 그룹이면 멈추고
    D 의 id 로 좁은 확인 finish 를 한 번 보낸다."""
    fake = _Who()
    config = WorkerConfig(
        server="http://srv", token="t", data_dir=str(tmp_path / "w"), lanes=1, grace_seconds=1
    )
    logs: list[str] = []
    w = RemoteWorker(config, client=fake, log=logs.append)
    w.config.data_path.mkdir(parents=True, exist_ok=True)
    grp = _spawn_group(["sleep", "30"])
    try:
        owner = rw.owner_of(w.config, "build-02")
        start = rw.process_start(grp.pid)
        assert start is not None
        d = _dead_folder(w, owner, {4: {"pgid": grp.pid, "start": start}})
        real = rw.process_start
        monkeypatch.setattr(rw, "process_start", lambda pid: None)  # ps · /proc 실패
        w.prepare_sessions()
        assert w.unverified_sessions == [d.name] and w.previous_sessions == []
        assert grp.poll() is None  # 손대지 않았다
        assert f"orphan #4: cannot verify process {grp.pid} — not touching it" in logs
        assert (
            f"dead session {d.name[:8]}: process identity unknown — keeping its group held,"
            " checking again later"
        ) in logs
        monkeypatch.setattr(rw, "process_start", real)  # 재확인 때는 읽힌다
        (dead,) = w.dead_sessions
        assert w.recheck(dead, 4) == rw.CONFIRMED
        assert _gone(grp)  # 같은 그룹이라 멈췄다
        assert any(m.startswith(f"orphan #4: stopped process group {grp.pid}") for m in logs)
        (sent,) = fake.finished
        assert sent[0] == d.name and sent[1] == 4
        assert sent[2]["summary_code"] == "worker_replaced" and sent[2]["group_gone"] is True
    finally:
        _kill(grp.pid)


def test_rl31_the_recheck_is_scheduled_at_ok_at_plus_g_minus_m(tmp_path, monkeypatch):
    fake = _Who()
    w = _worker(tmp_path, fake)
    owner = rw.owner_of(w.config, "build-02")
    d = _dead_folder(w, owner, {4: {"pgid": None, "start": None}})
    ok = w._epoch() - 100
    (d / "ok_at.json").write_text(
        json.dumps({"wall": datetime.fromtimestamp(ok, UTC).isoformat()}), encoding="utf-8"
    )
    timers: list[float] = []

    class T:
        def __init__(self, delay, fn, args=()):
            timers.append(delay)
            self.daemon = False
            self.name = ""

        def start(self):
            pass

    monkeypatch.setattr(rw.threading, "Timer", T)
    w.prepare_sessions()
    w.schedule_rechecks()
    (delay,) = timers
    assert abs(delay - (600 - 35 - 100)) < 2  # D.ok_at + G − m − 지금


def test_rl38_the_session_files_follow_the_job(tmp_path):
    fake = _Who()
    w = _worker(tmp_path, fake)
    w.prepare_sessions()
    w._write_claim(7, True, 600)
    w._write_proc(7, None)
    jd = w.session_dir / "jobs" / "7"
    assert json.loads((jd / "proc.json").read_text()) == {"pgid": None, "start": None}
    w._write_proc(7, os.getpid())
    proc = json.loads((jd / "proc.json").read_text())
    assert proc["pgid"] == os.getpid() and rw._start_valid(proc["start"])
    assert json.loads((jd / "claim.json").read_text()) == {
        "irreversible": True,
        "disconnect_grace_seconds": 600,
    }
    w.mark_ok(w._epoch(), 10_000.0)
    assert "wall" in json.loads((w.session_dir / "ok_at.json").read_text())
    w._forget_job_dir(7)
    assert not jd.exists()


# ── 관찰자의 기록 실패가 이미 띄운 프로세스를 버리지 않는다(P3 1라운드 HIGH · RL31 (c) · RL43) ──


class _Runs(_Who):
    """run_claimed 를 진짜 러너로 돌릴 만큼의 대역(phase · log 를 받는다)."""

    def phase(self, job_id: int, phase: str) -> dict:
        return {}

    def log(self, job_id: int, chunk: bytes, offset: int | None = None) -> dict:
        return {}

    def finish(self, job_id: int, outcome: str, rc: Any, summary: Any, **extra: Any) -> dict:
        self.finished.append(
            (self.session_id, job_id, {"outcome": outcome, "summary": summary, **extra})
        )
        return {}


def _claim_of(job_id: int, argv: list[str], *, irreversible: bool) -> dict[str, Any]:
    return {
        "job": {
            "id": job_id,
            "preset": "ship" if irreversible else "ok",
            "state": RUNNING,
            "irreversible": irreversible,
            "disconnect_grace_seconds": 600 if irreversible else None,
            "source": {"mode": "tree"},
        },
        "preset": {"name": "ship" if irreversible else "ok", "argv": argv},
    }


def _run_claimed(w: RemoteWorker, claim: dict[str, Any]) -> None:
    w._materialize = lambda spec, job: spec.workspace.mkdir(parents=True, exist_ok=True)
    t = threading.Thread(target=w.run_claimed, args=(1, claim), daemon=True)
    t.start()
    t.join(20)
    assert not t.is_alive()


@pytest.mark.parametrize("irreversible", [False, True])
def test_rl31_c_a_proc_record_that_cannot_be_written_after_popen_keeps_the_job_watched(
    tmp_path, monkeypatch, irreversible
):
    """Popen 뒤 `proc.json` 쓰기가 디스크 가득으로 실패해도 잡은 감시 · 보고되고 끝난다. 그때 남은
    기록은 「띄우는 중」(`pgid: null`)이라 다음 프로세스는 UNKNOWN 으로 읽는다 — 그룹을 사라졌다고
    판정하지 않는다."""
    fake = _Runs()
    logs: list[str] = []
    w = _worker(tmp_path, fake)
    w.log = logs.append
    w.prepare_sessions()
    real = rw._write_json
    seen: list[str] = []

    def full_disk(path: Path, data: dict[str, Any]) -> None:
        if path.name == "proc.json" and data.get("pgid") is not None:
            seen.append(rw.judge_orphan(path.parent, 1.0, logs.append))
            raise OSError(28, "No space left on device")
        real(path, data)

    monkeypatch.setattr(rw, "_write_json", full_disk)
    _run_claimed(w, _claim_of(7, ["sh", "-c", "echo ran"], irreversible=irreversible))
    assert seen == [rw.UNKNOWN]
    assert w.running == {}
    ((_, jid, body),) = fake.finished
    assert jid == 7 and body["outcome"] == SUCCEEDED
    if irreversible:
        assert body["group_gone"] is True
    assert any("#7 session folder: OSError" in m for m in logs)


def test_rl31_c_an_irreversible_job_whose_spawning_record_fails_starts_nothing(
    tmp_path, monkeypatch
):
    """「띄우는 중」 기록이 없으면 다음 프로세스는 「띄운 적 없음」(CONFIRMED)으로 읽는다 —
    그래서 표식 잡은 그 기록 없이 띄우지 않고 `launch_failed` 로 끝낸다(그룹 없음). 일반 잡은
    기록 없이 돈다."""
    fake = _Runs()
    w = _worker(tmp_path, fake)
    w.prepare_sessions()
    real = rw._write_json

    def full_disk(path: Path, data: dict[str, Any]) -> None:
        if path.name == "proc.json":
            raise OSError(28, "No space left on device")
        real(path, data)

    monkeypatch.setattr(rw, "_write_json", full_disk)
    ran = tmp_path / "ran"
    _run_claimed(w, _claim_of(7, ["touch", str(ran)], irreversible=True))
    assert len(fake.finished) == 1 and w.running == {}
    ((_, _, body),) = fake.finished
    assert body["outcome"] == "failed" and body["summary_code"] == "launch_failed"
    assert body["summary_args"] == {"error": "OSError"} and body["group_gone"] is True
    assert not ran.exists() and w.running == {}
    fake.finished.clear()
    _run_claimed(w, _claim_of(8, ["touch", str(ran)], irreversible=False))
    ((_, _, body),) = fake.finished
    assert body["outcome"] == SUCCEEDED and ran.exists() and w.running == {}


def test_rl44_a_finish_without_group_gone_keeps_the_folder_for_the_next_process(
    tmp_path, monkeypatch
):
    """`group_gone: false`(SIGKILL 뒤 2초 안에 그룹이 안 사라짐)면 서버는 홀드를 건다. 그 잡 폴더를
    지우면 다음 프로세스가 다시 판정할 `proc.json` 이 없어 「돌아온 워커가 그룹이 사라졌음을
    확인하면 홀드를 푼다」가 거짓이 된다(P3 1라운드 MED). 폴더를 남기고, 다음 프로세스는 그
    그룹이 사라졌음을 확인해 D 를 `previous_sessions` 로 넘긴다. 확인된 잡 · 일반 잡은 지운다."""
    from remote_ci_monitor.runner import RunResult

    gone = subprocess.Popen(["true"])
    gone.wait()
    answers = {7: False, 8: True, 9: False}

    def fake_run(spec, observer, **kw):
        observer.spawning()
        observer.spawned(gone.pid)
        at = kw["now_fn"]()
        return RunResult(rc=0, started=at, finished=at, group_gone=answers[spec.job_id])

    monkeypatch.setattr(rw, "run_job", fake_run)
    fake = _Runs()
    w = _worker(tmp_path, fake)
    w.prepare_sessions()
    _run_claimed(w, _claim_of(7, ["true"], irreversible=True))
    _run_claimed(w, _claim_of(8, ["true"], irreversible=True))
    _run_claimed(w, _claim_of(9, ["true"], irreversible=False))
    assert [(jid, body["group_gone"]) for _, jid, body in fake.finished] == [
        (7, False),
        (8, True),
        (9, False),
    ]
    jobs = w.session_dir / "jobs"
    assert sorted(p.name for p in jobs.iterdir()) == ["7"]
    assert json.loads((jobs / "7" / "proc.json").read_text())["pgid"] == gone.pid
    os.close(w._session_fd)  # 이 프로세스가 죽었다
    w2 = _worker(tmp_path, _Who())
    w2.prepare_sessions()
    assert [(d.session_id, d.verdicts) for d in w2.dead_sessions] == [
        (w.session_id, {7: rw.CONFIRMED})
    ]
    assert w2.previous_sessions == [w.session_id]


@pytest.mark.parametrize("irreversible", [False, True])
def test_rl43_any_observer_error_after_popen_stops_the_job_and_reports_it(
    tmp_path, monkeypatch, irreversible
):
    """펌프 안의 관찰자 호출이 어떤 예외를 내도 띄운 그룹을 끝내고 finish 를 보낸다 — 레인 루프가
    예외로 잡을 놓쳐 `running` 에 멈추지 않는다. 그룹이 사라졌음을 확인해 싣는다."""
    fake = _Runs()
    w = _worker(tmp_path, fake)
    w.prepare_sessions()
    pid_file = tmp_path / "pid"

    def broken(self, data: bytes) -> None:
        raise RuntimeError("observer broke")

    monkeypatch.setattr(rw._RemoteObserver, "output", broken)
    script = f"echo $$ > {pid_file}; echo hi; exec sleep 30"
    _run_claimed(w, _claim_of(7, ["sh", "-c", script], irreversible=irreversible))
    pid = int(pid_file.read_text())
    try:
        assert w.running == {}
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    finally:
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    ((_, jid, body),) = fake.finished
    assert jid == 7 and body["outcome"] == "failed"
    assert body["summary"] == "worker error: RuntimeError" and body["group_gone"] is True


@pytest.mark.parametrize("gone", [True, False])
def test_rl43_a_worker_error_after_the_runner_returned_reports_that_result(
    tmp_path, monkeypatch, gone
):
    """러너가 결과를 낸 뒤에 워커가 예외를 냈다 — 바깥 안전망은 늘 `failed` 를 보내지 않고 그
    결과와 그 `group_gone` 을 보낸다(P3 2라운드 LOW). 버리면 확인된 그룹도 홀드되고, 끝난 잡이
    실패로 남는다."""
    from remote_ci_monitor.runner import RunResult

    def fake_run(spec, observer, **kw):
        observer.spawning()
        at = kw["now_fn"]()
        return RunResult(rc=0, started=at, finished=at, group_gone=gone)

    def broken(*_a: Any, **_kw: Any) -> None:
        raise RuntimeError("collect broke")

    monkeypatch.setattr(rw, "run_job", fake_run)
    fake = _Runs()
    w = _worker(tmp_path, fake)
    w.prepare_sessions()
    monkeypatch.setattr(w, "_collect_artifacts", broken)
    _run_claimed(w, _claim_of(7, ["true"], irreversible=True))
    ((_, jid, body),) = fake.finished
    assert (jid, body["outcome"], body.get("group_gone")) == (7, SUCCEEDED, gone)
    assert "finished_at" in body and w.running == {}


def test_rl43_a_worker_error_before_anything_was_launched_says_the_group_is_gone(
    tmp_path, monkeypatch
):
    """띄우기 전(자재화)의 예외 — 바깥 안전망의 finish 가 `group_gone: true` 를 명시한다. 빼면
    서버는 띄운 적 없는 표식 잡의 그룹을 홀드한다."""
    fake = _Runs()
    w = _worker(tmp_path, fake)
    w.prepare_sessions()

    def broken(spec: Any, job: Any) -> None:
        raise RuntimeError("materialize broke")

    w._materialize = broken  # type: ignore[method-assign]
    t = threading.Thread(
        target=w.run_claimed, args=(1, _claim_of(7, ["true"], irreversible=True)), daemon=True
    )
    t.start()
    t.join(20)
    assert not t.is_alive()
    ((_, jid, body),) = fake.finished
    assert (jid, body["outcome"], body.get("group_gone")) == (7, "failed", True)
    assert body["summary"] == "worker error: RuntimeError" and w.running == {}


# ── admin 이 푸는 길(RL45 PR1 몫 · §4.12 · 결정 330) ────────────────────────────


def _held(srv: WorkerServer) -> int:
    srv.registered("build-02")
    jid = running(srv, preset="ship")
    srv.store.finish(jid, LOST, now=srv.clock(), summary="x", hold_cause="worker_unreachable")
    assert len(hold_rows(srv)) == 1
    return jid


def test_rl45_an_admin_releases_the_hold_with_a_cleaned_reason(srv):
    jid = _held(srv)
    waiting = srv.queued_job(preset="ship")
    assert srv.claim("build-02", 1)[0] == 204  # 홀드가 그룹을 막는다
    reason = "checked \x1b[31mbuild-02\x07: no process\n" + "x" * 300
    status, body = srv.req(
        "POST", f"/jobs/{jid}/release-hold", token="admin", json_body={"reason": reason}
    )
    assert status == 200
    assert set(body) == {"job_id", "group", "released_at", "released_by", "release_kind"}
    assert body["job_id"] == jid and body["group"] == "irreversible:ship"
    assert body["release_kind"] == "admin" and body["released_by"] == "macmini-admin"
    (h,) = hold_rows(srv)
    assert h["release_kind"] == "admin"
    assert len(h["release_reason"]) == 200
    assert not any(ord(c) < 32 or ord(c) == 127 for c in h["release_reason"])
    assert srv.claimed("build-02", 1) == waiting  # 그룹이 다시 열렸다


def test_rl45_release_hold_answers_403_404_and_409_no_hold(srv):
    jid = _held(srv)
    status, body = srv.req("POST", f"/jobs/{jid}/release-hold", token="alice", json_body={})
    assert status == 403
    assert hold_rows(srv)[0]["released_at"] is None
    status, body = srv.req("POST", "/jobs/999/release-hold", token="admin", json_body={})
    assert status == 404
    assert srv.req("POST", f"/jobs/{jid}/release-hold", token="admin", json_body={})[0] == 200
    status, body = srv.req("POST", f"/jobs/{jid}/release-hold", token="admin", json_body={})
    assert status == 409 and body["code"] == "no_hold" and "error" in body
    status, _ = srv.req("POST", f"/jobs/{jid}/release-hold", token="admin", json_body={"reason": 5})
    assert status == 400


class _HoldClient:
    server = "http://srv"
    token = "t"

    def __init__(self, answer: Any) -> None:
        self.answer = answer
        self.calls: list[tuple[int, str | None]] = []

    def release_hold(self, job_id: int, *, reason: str | None = None) -> dict[str, Any]:
        self.calls.append((job_id, reason))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.mark.parametrize(
    "answer, code",
    [
        ({"job_id": 7, "group": "g", "released_at": "t", "released_by": "a"}, 0),
        (ClientError(409, "job #7 has no open hold", {"code": "no_hold"}), 1),
        (ClientError(403, "admin token required"), 2),
        (ClientError(404, "no job #7"), 2),
        (ClientError(401, "token required"), 2),
        (ClientError(0, "connection refused"), 3),
    ],
)
def test_rl45_rcm_release_hold_exit_codes(monkeypatch, capsys, answer, code):
    from remote_ci_monitor import cli

    fake = _HoldClient(answer)
    monkeypatch.setattr(cli, "_client", lambda args: fake)
    assert cli.main(["release-hold", "7", "--reason", "checked", "--json"]) == code
    assert fake.calls == [(7, "checked")]
    out, err = capsys.readouterr()
    if code == 0:
        assert json.loads(out)["group"] == "g"
        assert "released the hold on group g (job #7)" in err


@pytest.mark.parametrize("status", [500, 502, 503])
def test_rl45_rcm_release_hold_reads_a_server_error_as_unknown(monkeypatch, capsys, status):
    """5xx 는 거절이 아니라 「모름」이다 — 2(사용 오류 · 401/403/404)가 아니라 3. 풀렸는지
    알 수 없으니 다시 해 볼 일이다(P3 1라운드 LOW)."""
    from remote_ci_monitor import cli

    fake = _HoldClient(ClientError(status, "internal error"))
    monkeypatch.setattr(cli, "_client", lambda args: fake)
    assert cli.main(["release-hold", "7"]) == 3


def test_rl45_rcm_release_hold_is_its_own_command_with_a_help_line(capsys):
    from remote_ci_monitor import cli

    with pytest.raises(SystemExit):
        cli.main(["--help"])
    assert "release-hold" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli.main(["release-hold", "--help"])
    out = capsys.readouterr().out
    assert "after you checked the machine" in out and "--reason" in out


# ── 홀드의 기계 판독 표면(RL45 의 PR1 몫 · §4.8 · 결정 330 「어디서나 needs_action」 · 350) ──

HOLD_ITEM_KEYS = {"job_id", "preset", "group", "scope", "worker", "cause", "since"}
HOLD_DOC_KEYS = {
    "cause",
    "created_at",
    "released_at",
    "release_kind",
    "released_by",
    "release_reason",
    "history",
}


@pytest.fixture
def srv2(tmp_path):
    s = WorkerServer(tmp_path, presets=[*PRESETS, sh("ship2", "echo ship2", irreversible=True)])
    yield s
    s.close()


def test_rl45_a_job_waiting_on_a_held_group_needs_action_with_the_hold_as_blocker(srv):
    jid = _held(srv)
    waiting = srv.queued_job(preset="ship")
    other = srv.queued_job(preset="ok")  # 다른 그룹 — 홀드와 무관
    row = srv.row(waiting)
    assert row["reason"] == "needs_action"
    assert row["action"]["code"] == "irreversible_hold"
    assert f"rcm release-hold {jid}" in row["action"]["note"]
    assert row["action"]["at"] is not None
    assert row["blocked_by"] == {
        "job_id": jid,
        "group": "irreversible:ship",
        "remaining_seconds": None,
    }
    assert row["estimate"]["finish_at"] is None
    assert row["estimate"]["wait_seconds"] is None
    assert srv.row(other)["reason"] != "needs_action"
    assert srv.row(other)["blocked_by"] is None
    # 풀면 그 사유가 사라진다
    assert srv.req("POST", f"/jobs/{jid}/release-hold", token="admin", json_body={})[0] == 200
    after = srv.row(waiting)
    assert after["reason"] != "needs_action" and after["action"] is None


def test_rl45_status_and_health_list_every_open_hold_and_health_stays_as_it_was(srv2):
    srv = srv2
    srv.registered("build-02")
    code0, health0 = srv.req("GET", "/api/health")
    assert health0["irreversible_holds"] == []
    assert srv.status()["irreversible_holds"] == []
    a = _held(srv)
    b = running(srv, preset="ship2")
    srv.store.finish(b, LOST, now=srv.clock(), summary="x", hold_cause="orphan_unverified")
    srv.queued_job(preset="ship")  # 첫 홀드에만 대기 잡이 있다
    holds = srv.status()["irreversible_holds"]
    assert [h["job_id"] for h in holds] == [a, b]
    assert all(set(h) == HOLD_ITEM_KEYS for h in holds)
    assert holds[0]["group"] == "irreversible:ship" and holds[0]["scope"] == "global"
    assert holds[0]["preset"] == "ship" and holds[0]["worker"] == "build-02"
    assert holds[1]["cause"] == "orphan_unverified"
    assert holds[0]["since"].endswith("Z")
    code1, health1 = srv.req("GET", "/api/health")
    assert [h["job_id"] for h in health1["irreversible_holds"]] == [a, b]
    # 정보일 뿐이다 — 홀드로 ok · 503 이 바뀌지 않는다
    assert (code1, health1["ok"]) == (code0, health0["ok"])
    srv.req("POST", f"/jobs/{a}/release-hold", token="admin", json_body={})
    assert [h["job_id"] for h in srv.status()["irreversible_holds"]] == [b]


def test_rl45_status_says_null_not_empty_when_the_holds_cannot_be_read(srv, monkeypatch):
    """홀드를 못 읽으면 `irreversible_holds` 는 모름(null)이다 — `[]` 은 「열린 홀드 없음」이라는
    거짓말이다(`/api/health` 와 같은 규칙, P3 1라운드 LOW)."""
    import sqlite3

    srv.registered("build-02")
    _held(srv)

    def broken() -> list:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(srv.store, "open_holds", broken)
    srv.app._mark_dirty()
    assert srv.status()["irreversible_holds"] is None
    assert srv.req("GET", "/api/health")[1]["irreversible_holds"] is None


def test_rl44_c_a_finish_racing_the_janitor_close_gets_job_closed_and_releases(srv, monkeypatch):
    """조기 확인(잡이 busy)과 `store.finish` 사이에 janitor 가 잡을 닫았다. 그 응답도 늦은 보고와
    같아야 한다 — 409 `code: job_closed`, 그리고 `group_gone: true` 면 방금 걸린 홀드를 푼다
    (P3 1라운드 LOW). 다르면 워커는 그 409 를 닫힌 잡으로 읽지 못하고 홀드가 남는다."""
    srv.registered("build-02")
    jid = running(srv, preset="ship")
    real_finish = srv.store.finish
    raced: list[bool] = []

    def janitor_first(job_id: int, *args: Any, **kw: Any):
        if not raced:
            raced.append(
                real_finish(
                    job_id,
                    LOST,
                    now=srv.clock(),
                    summary="x",
                    only_from=(RUNNING,),
                    hold_cause="worker_unreachable",
                )
            )
        return real_finish(job_id, *args, **kw)

    monkeypatch.setattr(srv.store, "finish", janitor_first)
    status, body = srv.req(
        "POST",
        f"/worker/jobs/{jid}/finish",
        token="build-02",
        json_body={"outcome": "succeeded", "exit_code": 0, "group_gone": True},
    )
    assert raced == [True]
    assert (status, body.get("code"), body.get("hold_released")) == (409, "job_closed", True), body
    assert job_of(srv, jid).state == LOST
    (h,) = hold_rows(srv)
    assert h["released_at"] is not None and h["release_kind"] == "worker"


def test_rl44_c_a_preflight_finish_racing_a_cancel_and_then_the_janitor_gets_job_closed(
    srv, monkeypatch
):
    """두 번째 경합 닫기 자리(P3 2라운드 LOW). 시작 전 실패의 finish(`only_from=running`)를 취소가
    이겨 재시도(`only_from=cancelling`)하는데, 그 사이 janitor 가 잡을 닫았다. 그 응답도 늦은
    보고와 같다 — 409 `code: job_closed`, `group_gone: true` 면 방금 걸린 홀드를 푼다."""
    srv.registered("build-02")
    jid = running(srv, preset="ship")
    real_finish = srv.store.finish
    calls: list[Any] = []

    def racing(job_id: int, *args: Any, **kw: Any):
        calls.append(kw.get("only_from"))
        if len(calls) == 1:  # 취소가 끼어든다 — 첫 닫기는 거절된다
            assert srv.store.request_cancel(job_id, "alice-laptop", srv.clock(), 10) == CANCELLING
        elif len(calls) == 2:  # 재시도 직전에 janitor 가 닫는다
            assert real_finish(
                job_id,
                LOST,
                now=srv.clock(),
                summary="x",
                only_from=(CANCELLING,),
                hold_cause="worker_unreachable",
            )
        return real_finish(job_id, *args, **kw)

    monkeypatch.setattr(srv.store, "finish", racing)
    status, body = srv.req(
        "POST",
        f"/worker/jobs/{jid}/finish",
        token="build-02",
        json_body={
            "outcome": "failed",
            "exit_code": None,
            "summary_code": "launch_failed",
            "summary_args": {"error": "OSError"},
            "group_gone": True,
        },
    )
    assert calls == [(RUNNING,), (CANCELLING,)]
    assert (status, body.get("code"), body.get("hold_released")) == (409, "job_closed", True), body
    assert job_of(srv, jid).state == LOST
    (h,) = hold_rows(srv)
    assert h["released_at"] is not None and h["release_kind"] == "worker"


def test_rl45_the_job_document_carries_the_hold_and_its_release(srv):
    srv.registered("build-02")
    plain = running(srv, preset="ok")
    srv.finish("build-02", plain, "succeeded", exit_code=0)
    assert srv.req("GET", f"/jobs/{plain}", token="alice")[1]["hold"] is None
    jid = _held(srv)
    doc = srv.req("GET", f"/jobs/{jid}", token="alice")[1]
    assert set(doc["hold"]) == HOLD_DOC_KEYS
    assert doc["hold"]["cause"] == "worker_unreachable"
    assert doc["hold"]["released_at"] is None
    assert [h["action"] for h in doc["hold"]["history"]] == ["created"]
    srv.req("POST", f"/jobs/{jid}/release-hold", token="admin", json_body={"reason": "ps -g: none"})
    hold = srv.req("GET", f"/jobs/{jid}", token="alice")[1]["hold"]
    assert hold["released_by"] == "macmini-admin"
    assert hold["release_reason"] == "ps -g: none"
    assert hold["release_kind"] == "admin" and hold["released_at"].endswith("Z")
    assert [h["action"] for h in hold["history"]] == ["created", "released"]
    # 대기 잡 문서도 `hold` 키를 싣는다(그 잡 자신의 홀드 — 없으면 null)
    waiting = srv.queued_job(preset="ship")
    assert srv.req("GET", f"/jobs/{waiting}", token="alice")[1]["hold"] is None


# ── P2-5: 계획↔시험 대조가 약하다고 한 행(RL11 · RL24 · RL44) ──────────────────────


def _released_events(srv: WorkerServer, jid: int) -> int:
    return sum(
        1
        for r in srv.store._conn().execute(
            "SELECT payload FROM events WHERE job_id=? AND kind='hold' ORDER BY id", (jid,)
        )
        if json.loads(r["payload"])["action"] == "released"
    )


def _log_sha(srv: WorkerServer, jid: int) -> str:
    import hashlib

    path = srv.app.job_dir(jid) / "log.txt"
    return hashlib.sha256(path.read_bytes() if path.exists() else b"").hexdigest()


def test_rl11_a_fenced_sessions_late_reports_change_nothing_and_its_peer_closes_on_time(srv):
    """펜싱된 A 의 늦은 log · phase · finish · 산출물 업로드 · capabilities 는 409 `session_fenced`
    이고 log.txt 의 sha256 · 마커(`last_step`) · 산출물 · 능력 보고 · `late_result` 가 그대로다.
    다른 이름의 토큰이 남의 세션을 실어 403 을 받은 뒤에도 그 세션의 표식 잡은 G 에 닫힌다."""
    srv.registered("build-02")
    jid = running(srv, preset="slow")
    assert srv.log("build-02", jid, b"::rcm::step::real\nline\n")[0] == 200
    old = srv.session("build-02")
    srv.new_process("build-02")
    srv.registered("build-02", lanes=2)  # B 가 A 를 펜싱한다 — A 의 잡은 A 의 세션에 남는다
    before_job = srv.view(jid, token="alice")
    before_sha = _log_sha(srv, jid)
    before_workers = srv.workers()
    sess = {"X-RCM-Worker-Session": old}
    answers = [
        srv.req(
            "POST",
            f"/worker/jobs/{jid}/log",
            token="build-02",
            body=b"::rcm::step::evil\n::rcm::summary::forged\n",
            headers={"Content-Type": "application/octet-stream", **sess},
        ),
        srv.req(
            "POST",
            f"/worker/jobs/{jid}/phase",
            token="build-02",
            json_body={"phase": "running", "worker_session_id": old},
        ),
        srv.req(
            "POST",
            f"/worker/jobs/{jid}/finish",
            token="build-02",
            json_body={"outcome": "succeeded", "exit_code": 0, "worker_session_id": old},
        ),
        srv.req(
            "PUT",
            f"/worker/jobs/{jid}/artifacts",
            token="build-02",
            body=b"not-a-tar",
            headers={"Content-Type": "application/x-tar", **sess},
        ),
        srv.req(
            "POST",
            "/worker/capabilities",
            token="build-02",
            json_body={"worker_session_id": old, "generation": "x", "presets": {}},
        ),
    ]
    for status, body in answers:
        assert status == 409 and body["code"] == "session_fenced", (status, body)
    after_job = srv.view(jid, token="alice")
    assert _log_sha(srv, jid) == before_sha
    for key in ("state", "last_step", "summary", "artifacts", "late_result", "exit_code"):
        assert after_job.get(key) == before_job.get(key), key
    assert srv.workers() == before_workers
    # 남의 토큰이 B 의 세션을 실은 heartbeat → 403. 그 뒤로 B 가 조용하면 표식 잡이 G 에 닫힌다
    srv.registered("build-03")
    theirs = srv.session("build-02")
    ship = running(srv, preset="ship", lane=2)
    for _ in range(3):
        srv.clock.advance(250)
        status, body = srv.req(
            "POST",
            "/worker/heartbeat",
            token="build-03",
            json_body={
                "worker_session_id": theirs,
                "seq": 99,
                "claims_settled": 99,
                "jobs": [ship],
            },
        )
        assert status == 403, body
        srv.app.mark_lost_workers(srv.clock.now)
    j = job_of(srv, ship)
    assert j.state == LOST and j.summary_code == "worker_unreachable"
    assert [h["job_id"] for h in hold_rows(srv)] == [ship]


def test_rl24_a_quiet_legacy_job_closes_at_heard_legacy_plus_g_and_holds_its_preset_group(srv):
    srv.store.register_worker(
        "build-02", pool="default", lanes=1, host_name=None, version=__version__, now=srv.clock()
    )
    jid = _legacy_running(srv, preset="ship")
    log_dir = srv.app.job_dir(jid)
    status, _ = srv.req(
        "POST", "/worker/heartbeat", token="build-02", json_body={"jobs": [jid]}, legacy=True
    )
    assert status == 200  # heard_legacy(build-02) 가 오른다
    srv.clock.advance(599)
    assert srv.app.mark_lost_workers(srv.clock.now) == []
    assert job_of(srv, jid).state == RUNNING
    srv.clock.advance(2)  # heard_legacy + G(600) 를 넘었다
    assert srv.app.mark_lost_workers(srv.clock.now) == [jid]
    j = job_of(srv, jid)
    assert j.state == LOST and j.summary_code == "worker_unreachable"
    (h,) = hold_rows(srv)
    assert h["job_id"] == jid and h["concurrency_group"] == "irreversible:ship"
    assert srv.app.job_dir(jid) == log_dir  # 로그 경로는 그대로다


def test_rl44_hold_release_events_are_one_for_a_b_c_and_zero_for_d(srv, tmp_path):
    """(a) previous_sessions · (b) 재확인의 좁은 확인 · (c) 늦은 finish(`group_gone: true`) 는
    사건 `hold`(released) 를 1건씩 남기고, (d) 다른 이름의 previous_sessions 는 0건이다."""
    srv.registered("build-02")
    srv.registered("build-03")
    # (d) 먼저 — 다른 이름은 풀지 못하고 사건도 없다
    d = running(srv, preset="ship")
    srv.store.finish(d, LOST, now=srv.clock(), summary="x", hold_cause="worker_unreachable")
    old = srv.session("build-02")
    srv.new_process("build-03")
    assert srv.register("build-03", previous_sessions=[old])[0] == 403
    assert _released_events(srv, d) == 0
    # (a) 같은 이름의 새 프로세스가 D 의 그룹이 없음을 확인한다
    srv.restarted("build-02")
    assert _released_events(srv, d) == 1
    # (c) 같은 세션의 늦은 finish 가 group_gone: true
    c = running(srv, preset="ship")
    srv.store.finish(c, LOST, now=srv.clock(), summary="x", hold_cause="worker_unreachable")
    status, body = srv.req(
        "POST",
        f"/worker/jobs/{c}/finish",
        token="build-02",
        json_body={"outcome": "lost", "exit_code": None, "group_gone": True},
    )
    assert status == 409 and body["hold_released"] is True
    assert _released_events(srv, c) == 1
    # (b) 죽은 세션의 잡을 재확인이 「확인됨」으로 닫는 좁은 확인
    b = running(srv, preset="ship")
    srv.store.finish(b, LOST, now=srv.clock(), summary="x", hold_cause="orphan_unverified")
    dead = srv.new_process("build-02")
    srv.registered("build-02", unverified_sessions=[dead])
    status, body = srv.req(
        "POST",
        f"/worker/jobs/{b}/finish",
        token="build-02",
        json_body={
            "outcome": "lost",
            "exit_code": None,
            "summary_code": "worker_replaced",
            "summary_args": {},
            "group_gone": True,
            "worker_session_id": dead,
        },
    )
    assert body.get("hold_released") is True, (status, body)
    assert _released_events(srv, b) == 1
    assert _released_events(srv, d) == 1 and _released_events(srv, c) == 1  # 다시 세지 않는다


# ── 기준선(RL6 (a)) ─────────────────────────────────────────────────────────

#: 36069787(이 PR 의 base, v0.11.1)의 종료 원격 잡 문서 키 — `rcm wait` JSON 의 몸통이다. 이 PR 은
#: `App._with_hold` 로 `hold` 하나만 더한다(`git diff 36069787 -- src/remote_ci_monitor/server.py
#: src/remote_ci_monitor/core/status.py` 의 잡 문서 쪽 변경은 그것뿐이다). 잠금이다.
BASE_TERMINAL_DOC_KEYS = frozenset(
    (
        "action",
        "artifacts",
        "attempt",
        "cancelled_by",
        "chain_id",
        "concurrent_at_start",
        "created_at",
        "display_pool",
        "exit_code",
        "failed_step",
        "finished_at",
        "host_key",
        "id",
        "inputs",
        "job_seconds",
        "joiners",
        "keep",
        "key",
        "last_step",
        "link",
        "max_attempts",
        "not_before",
        "placement",
        "pool",
        "preset",
        "ran_on",
        "requester",
        "retried_as",
        "retry_of",
        "shelf",
        "source",
        "start_deadline",
        "started_at",
        "state",
        "step_timeline",
        "summary",
        "summary_args",
        "summary_code",
        "superseded_by",
        "target_worker",
        "timeout_seconds",
        "transitions",
        "url",
        "waited_seconds",
        "worker",
    )
)
#: 실패 · 시간초과 잡에만 있던 키(같은 base)
BASE_FAILURE_KEYS = frozenset(("failures", "failures_truncated"))


@pytest.mark.parametrize(
    ("outcome", "exit_code", "wait_code"),
    [("succeeded", 0, 0), ("failed", 1, 1), ("timed_out", None, 2), ("cancelled", -15, 2)],
)
def test_rl6_a_an_unbroken_remote_job_keeps_its_exit_code_and_the_wait_json_keys(
    srv, monkeypatch, capsys, outcome, exit_code, wait_code
):
    """(a) 끊김 없는 원격 잡의 성공 · 실패 · 시간초과 · 취소: `rcm wait` 종료 코드는 0 · 1 · 2 · 2
    그대로이고, wait JSON 은 기존 키를 모두 지닌 채 `hold`(null)만 더한다."""
    srv.registered("build-02")
    jid = running(srv)
    if outcome == "cancelled":
        assert srv.cancel(jid)[1]["state"] == "cancelling"
        assert srv.heartbeat("build-02", jobs=[jid])[1]["cancel"] == [jid]
    assert srv.finish("build-02", jid, outcome, exit_code=exit_code)[0] == 200
    url = f"http://127.0.0.1:{srv.port}"
    monkeypatch.setattr(cli, "_client", lambda *a, **kw: Client(url, srv.tokens["alice"]))
    args = cli.argparse.Namespace(job=jid, timeout=30, poll=True, return_on_action=False)
    assert cli.cmd_wait(args) == wait_code
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("{")]
    got = json.loads(lines[-1])
    assert got["state"] == outcome and got["wait_exit_code"] == wait_code
    keys = set(got) - {"job_id", "wait_exit_code"}  # `rcm wait` 가 늘 더하던 두 키
    failing = outcome in ("failed", "timed_out")
    base = BASE_TERMINAL_DOC_KEYS | (BASE_FAILURE_KEYS if failing else frozenset())
    assert base - keys == set()  # 기존 키 불변
    # 더한 키만 있다 — PR2 가 `late_result` · `log_complete` 를 더했다(M5t-b §4.6)
    assert keys - base == {"hold", "late_result", "log_complete"}
    assert got["hold"] is None
