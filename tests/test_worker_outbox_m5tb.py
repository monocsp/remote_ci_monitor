"""finish · 늦은 결과 · 로그 완전성 · 산출물 처분 (M5t-b §4.6 · 결정 256 · 259 · 274 · 275 · 277 ·
330) 과 워커의 디스크 보고 큐 · 잡마다 보내는 스레드 · 죽은 세션 큐(§4.4 의 7 · §4.5 · 결정 264 ·
271 · 324 · 326 · 337).

서버 쪽은 in-process HTTP(`test_worker_api.WorkerServer`, 주입 시계)다. 유예 · 취소로 닫히는 것은
`store.finish` 를 직접 불러 흉내 낸다(janitor 와 같은 호출이다). 워커 쪽은 가짜 서버(`FakeServer`)
가 요청마다 주입한 벽시계를 민다 — 300초 · 24시간 판정은 그 시계로 한다.
"""

from __future__ import annotations

import hashlib
import io
import os
import tarfile
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from remote_ci_monitor import remote_worker as rw
from remote_ci_monitor.client import ClientError
from remote_ci_monitor.config import WorkerConfig
from remote_ci_monitor.core.model import LOST, RUNNING, SUCCEEDED
from remote_ci_monitor.core.status import iso
from test_worker_api import OCTET, WORKER_PRESETS, WorkerServer, sh

W = "build-02"
PRESETS = [
    *WORKER_PRESETS,
    sh("ship", "echo ship", irreversible=True),
    sh("arts", "echo arts", artifacts=["out/**"]),
]


@pytest.fixture
def srv(tmp_path):
    s = WorkerServer(tmp_path, presets=PRESETS)
    yield s
    s.close()


def running(srv: WorkerServer, preset: str = "ok") -> int:
    if W not in srv.sessions:
        srv.registered(W)
    jid = srv.queued_job(preset=preset)
    assert srv.claimed(W) == jid
    return jid


def fin(srv: WorkerServer, jid: int, outcome: str = SUCCEEDED, **body: Any) -> tuple[int, Any]:
    body.setdefault("exit_code", 0 if outcome == SUCCEEDED else None)
    payload = {"outcome": outcome, **body}
    return srv.req("POST", f"/worker/jobs/{jid}/finish", token=W, json_body=payload)


def post_log(srv: WorkerServer, jid: int, data: bytes, offset: int) -> tuple[int, Any]:
    headers = {**OCTET, "X-RCM-Log-Offset": str(offset)}
    return srv.req("POST", f"/worker/jobs/{jid}/log", token=W, body=data, headers=headers)


def close_lost(srv: WorkerServer, jid: int, *, hold: str | None = None) -> None:
    """서버가 먼저 닫는다(유예 만료 흉내)."""
    from remote_ci_monitor.core import outcome

    text, code, args = outcome.summary("worker_unreachable", name=W, seconds=90)
    assert srv.store.finish(
        jid,
        LOST,
        now=srv.clock(),
        summary=text,
        summary_code=code,
        summary_args=args,
        hold_cause=hold,
    )


def late_events(srv: WorkerServer, jid: int) -> list[dict[str, Any]]:
    rows = srv.store._conn().execute(
        "SELECT payload FROM events WHERE job_id=? AND kind='late_result'", (jid,)
    )
    return [dict(r) for r in rows]


def job_doc(srv: WorkerServer, jid: int) -> dict[str, Any]:
    status, doc = srv.req("GET", f"/jobs/{jid}", token="alice")
    assert status == 200, doc
    return doc


# ── 늦은 결과(T14 · RL4 · RL5 · RL8 · RL21) ────────────────────────────────────


def test_rl21_b_late_finish_after_lost_is_recorded_once_and_changes_nothing(srv):
    jid = running(srv)
    assert post_log(srv, jid, b"one\n", 0)[0] == 200
    srv.clock.advance(30)
    close_lost(srv, jid)
    before = srv.store.get_job(jid)
    srv.clock.advance(5)
    status, body = fin(srv, jid, SUCCEEDED, log_bytes=4, group_gone=True)
    assert status == 409 and body["code"] == "job_closed" and body["state"] == LOST, body
    assert body["late_result_recorded"] is True and body["hold_released"] is False, body
    after = srv.store.get_job(jid)
    assert (after.state, after.summary, after.finished_at) == (
        before.state,
        before.summary,
        before.finished_at,
    )
    doc = job_doc(srv, jid)
    assert doc["state"] == LOST and doc["summary_code"] == "worker_unreachable"
    late = doc["late_result"]
    assert set(late) == {"outcome", "exit_code", "finished_at", "received_at", "log_complete"}
    assert (late["outcome"], late["exit_code"], late["log_complete"]) == (SUCCEEDED, 0, True)
    assert late["received_at"] == iso(srv.clock())
    assert doc["log_complete"] is True
    # 같은 보고가 또 와도(재시작한 워커의 큐) 한 건이다
    status, body = fin(srv, jid, SUCCEEDED, log_bytes=4, group_gone=True)
    assert status == 409 and body["late_result_recorded"] is False, body
    assert len(late_events(srv, jid)) == 1


def test_rl4_late_finish_never_flips_lost(srv):
    """`lost` 가 된 뒤 같은 잡이 `succeeded` 등으로 뒤집히지 않는다(늦은 finish 는 기록만)."""
    for outcome in (SUCCEEDED, "failed", "timed_out", "cancelled"):
        jid = running(srv)
        close_lost(srv, jid)
        fin(srv, jid, outcome, exit_code=0 if outcome == SUCCEEDED else 1, group_gone=True)
        assert srv.store.get_job(jid).state == LOST, outcome
        assert len(late_events(srv, jid)) == 1


def test_rl21_c_two_concurrent_late_finishes_record_exactly_one(srv):
    jid = running(srv)
    close_lost(srv, jid)
    answers: list[Any] = []
    barrier = threading.Barrier(2)

    def send() -> None:
        barrier.wait(5)
        answers.append(fin(srv, jid, SUCCEEDED, group_gone=True))

    threads = [threading.Thread(target=send) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(15)
    assert len(late_events(srv, jid)) == 1
    flags = sorted(a[1]["late_result_recorded"] for a in answers)
    assert flags == [False, True], answers


def test_rl21_a_duplicate_finish_is_409_and_records_nothing(srv):
    jid = running(srv)
    assert fin(srv, jid, SUCCEEDED, log_bytes=0, group_gone=True)[0] == 200
    first = srv.store.get_job(jid)
    srv.clock.advance(10)
    status, body = fin(srv, jid, SUCCEEDED, log_bytes=0, group_gone=True)
    assert status == 409 and body["code"] == "job_closed", body
    assert body.get("late_result_recorded") is False
    assert srv.store.get_job(jid).finished_at == first.finished_at
    assert late_events(srv, jid) == [] and job_doc(srv, jid)["late_result"] is None


def test_rl11_fenced_sessions_late_finish_is_not_recorded(srv):
    jid = running(srv)
    old = srv.session(W)
    close_lost(srv, jid)
    srv.new_process(W)
    srv.registered(W)  # 옛 세션을 펜싱한다
    body = {"outcome": SUCCEEDED, "exit_code": 0, "worker_session_id": old}
    status, answer = srv.req("POST", f"/worker/jobs/{jid}/finish", token=W, json_body=body)
    assert (status, answer.get("code")) == (409, "session_fenced"), answer
    assert late_events(srv, jid) == []


def test_rl11_the_store_refuses_a_late_result_from_a_fenced_session(srv):
    """펜싱된 세션의 늦은 finish 는 라우트에서 좁은 확인 길로 가지만, 기록하는 트랜잭션도 펜싱을
    다시 본다(2차 Q-B) — 늦은 결과를 남기지 않는다."""
    jid = running(srv)
    old = srv.session(W)
    close_lost(srv, jid)
    srv.new_process(W)
    srv.registered(W)
    got = srv.store.record_late_result(
        jid,
        session=old,
        payload={"outcome": SUCCEEDED, "exit_code": 0},
        log_bytes=None,
        gone=True,
        by=W,
        now=srv.clock(),
    )
    assert got is None and late_events(srv, jid) == []


def test_rl44_c_late_finish_with_group_gone_releases_the_hold_in_the_same_answer(srv):
    jid = running(srv, "ship")
    close_lost(srv, jid, hold="worker_unreachable")
    assert srv.store.has_open_hold(jid)
    status, body = fin(srv, jid, SUCCEEDED, group_gone=True)
    assert status == 409 and body["late_result_recorded"] is True, body
    assert body["hold_released"] is True
    assert not srv.store.has_open_hold(jid)
    assert len(late_events(srv, jid)) == 1


def test_rl44_c_late_finish_without_group_gone_keeps_the_hold(srv):
    jid = running(srv, "ship")
    close_lost(srv, jid, hold="worker_unreachable")
    status, body = fin(srv, jid, SUCCEEDED)
    assert body["late_result_recorded"] is True and body["hold_released"] is False, body
    assert srv.store.has_open_hold(jid)


def test_rl20_late_result_log_complete_is_false_when_bytes_are_missing(srv):
    jid = running(srv)
    assert post_log(srv, jid, b"abc\n", 0)[0] == 200
    close_lost(srv, jid)
    status, body = fin(srv, jid, SUCCEEDED, log_bytes=40, group_gone=True)
    assert body["late_result_recorded"] is True
    doc = job_doc(srv, jid)
    assert doc["late_result"]["log_complete"] is False and doc["log_complete"] is False


# ── finished_at 자르기(결정 277 · RL27 b) ─────────────────────────────────────


@pytest.mark.parametrize("skew", [3600, -3600])
def test_rl27_b_worker_finished_at_is_clamped_into_started_and_received(srv, skew):
    jid = running(srv)
    srv.clock.advance(20)
    worker_clock = srv.clock() + timedelta(seconds=skew)
    assert fin(srv, jid, SUCCEEDED, finished_at=iso(worker_clock), group_gone=True)[0] == 200
    job = srv.store.get_job(jid)
    assert job.started_at <= job.finished_at <= srv.clock()
    doc = job_doc(srv, jid)
    assert doc["job_seconds"] >= 0
    assert doc["finished_at"] == iso(srv.clock() if skew > 0 else job.started_at)


def test_rl27_b_late_result_finished_at_is_clamped_too(srv):
    jid = running(srv)
    close_lost(srv, jid)
    future = srv.clock() + timedelta(hours=1)
    fin(srv, jid, SUCCEEDED, finished_at=iso(future), group_gone=True)
    assert job_doc(srv, jid)["late_result"]["finished_at"] == iso(srv.clock())


# ── 로그 완전성(§4.6 · RL20) ─────────────────────────────────────────────────


def test_rl20_finish_with_fewer_bytes_received_than_produced_is_log_incomplete(srv):
    jid = running(srv)
    assert post_log(srv, jid, b"abc\n", 0)[0] == 200
    assert fin(srv, jid, SUCCEEDED, log_bytes=12 * 1024 * 1024, group_gone=True)[0] == 200
    doc = job_doc(srv, jid)
    assert doc["log_complete"] is False
    recent = {r["id"]: r for p in srv.status()["pools"] for r in p["recent"] or []}
    assert recent[jid]["log_complete"] is False


def test_rl20_finish_with_every_byte_received_is_log_complete_and_legacy_is_unknown(srv):
    jid = running(srv)
    assert post_log(srv, jid, b"abc\n", 0)[0] == 200
    assert fin(srv, jid, SUCCEEDED, log_bytes=4, group_gone=True)[0] == 200
    assert job_doc(srv, jid)["log_complete"] is True
    other = running(srv)
    assert fin(srv, other, SUCCEEDED, group_gone=True)[0] == 200  # log_bytes 없음(옛 워커 꼴)
    assert job_doc(srv, other)["log_complete"] is None


def test_rl20_a_a_local_lane_job_is_log_complete(srv):
    """로컬 레인 잡은 서버가 직접 쓰므로 `log_complete` 가 참이다(§4.6)."""
    jid = srv.queued_job()
    claimed = srv.store.claim(1, srv.clock())
    assert claimed is not None and claimed.id == jid
    assert srv.store.finish(jid, SUCCEEDED, now=srv.clock())
    assert srv.store.log_complete_for([jid]) == {jid: True}
    assert job_doc(srv, jid)["log_complete"] is True


# ── 산출물 처분과 `ready` 조건(결정 275 · F63 · RL22) ────────────────────────


def bundle_bytes() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        info = tarfile.TarInfo("out/a.txt")
        info.size = 2
        tf.addfile(info, io.BytesIO(b"hi"))
    return buf.getvalue()


def upload(srv: WorkerServer, jid: int, data: bytes) -> tuple[int, Any]:
    headers = {"Content-Type": "application/x-tar"}
    return srv.req("PUT", f"/worker/jobs/{jid}/artifacts", token=W, body=data, headers=headers)


def ready(digest: str | None) -> dict[str, Any]:
    return {"state": "ready", "file_count": 1, "total_bytes": 2, "bundle_sha256": digest}


def test_rl22_d_ready_without_an_upload_is_upload_failed(srv):
    jid = running(srv, "arts")
    digest = hashlib.sha256(bundle_bytes()).hexdigest()
    assert fin(srv, jid, SUCCEEDED, artifacts=ready(digest), group_gone=True)[0] == 200
    doc = job_doc(srv, jid)
    assert doc["state"] == SUCCEEDED  # 잡 결과는 산출물과 무관하다
    assert (doc["artifacts"]["state"], doc["artifacts"]["reason_code"]) == (
        "failed",
        "upload_failed",
    ), doc["artifacts"]


def test_rl22_e_ready_with_the_row_but_no_file_is_upload_failed(srv):
    jid = running(srv, "arts")
    data = bundle_bytes()
    status, receipt = upload(srv, jid, data)
    assert status == 201, receipt
    (srv.app.artifacts_dir(jid) / "bundle.tar").unlink()
    assert fin(srv, jid, SUCCEEDED, artifacts=ready(receipt["bundle_sha256"]))[0] == 200
    a = job_doc(srv, jid)["artifacts"]
    assert (a["state"], a["reason_code"]) == ("failed", "upload_failed"), a


def test_rl22_ready_with_a_different_hash_is_hash_mismatch(srv):
    jid = running(srv, "arts")
    status, receipt = upload(srv, jid, bundle_bytes())
    assert status == 201
    assert fin(srv, jid, SUCCEEDED, artifacts=ready("0" * 64), group_gone=True)[0] == 200
    a = job_doc(srv, jid)["artifacts"]
    assert (a["state"], a["reason_code"]) == ("failed", "hash_mismatch"), a


def test_rl22_b_reupload_is_idempotent_and_ready_needs_row_hash_and_file(srv):
    jid = running(srv, "arts")
    data = bundle_bytes()
    first = upload(srv, jid, data)
    again = upload(srv, jid, data)  # 응답을 잃어 다시 올린다
    assert first[0] == 201 and again[0] == 200, (first, again)
    assert first[1]["bundle_sha256"] == again[1]["bundle_sha256"]
    assert fin(srv, jid, SUCCEEDED, artifacts=ready(first[1]["bundle_sha256"]))[0] == 200
    a = job_doc(srv, jid)["artifacts"]
    assert a["state"] == "ready", a
    count = srv.store._conn().execute("SELECT COUNT(*) FROM job_artifacts WHERE job_id=?", (jid,))
    assert count.fetchone()[0] == 1
    assert srv.store.get_job(jid).state != RUNNING


# ══ 워커 쪽: 디스크 큐 · 잡마다 보내는 스레드 · 죽은 세션 큐(§4.4 의 7 · §4.5) ═══════════════════

T0W = datetime(2026, 9, 6, 12, 0, 0, tzinfo=UTC)


class WClock:
    """워커의 벽시계 — 가짜 서버가 요청마다 민다."""

    def __init__(self) -> None:
        self.wall = T0W
        self.mono = 1000.0

    def now(self) -> datetime:
        return self.wall

    def mono_fn(self) -> float:
        return self.mono

    def advance(self, seconds: float) -> None:
        self.wall += timedelta(seconds=seconds)
        self.mono += seconds


class FakeServer:
    """`/worker/*` 대역 — 로그는 서버의 오프셋 규칙(§4.5 step 5)으로 받는다. `script[route]` 의
    앞에서부터 하나씩 꺼내 예외면 던진다. 요청마다 `tick` 초씩 시계를 민다."""

    server = "http://fake"

    def __init__(self, clock: WClock, *, tick: float = 0.0) -> None:
        self.clock = clock
        self.tick = tick
        self.session_id: str | None = "a" * 32
        self.calls: list[tuple[Any, ...]] = []
        self.script: dict[str, list[Any]] = {}
        self.received: dict[int, bytearray] = {}
        self.lock = threading.Lock()

    def __copy__(self) -> FakeServer:
        other = FakeServer.__new__(FakeServer)
        other.__dict__.update(self.__dict__)
        return other

    def _next(self, route: str) -> None:
        self.clock.advance(self.tick)
        queue = self.script.get(route) or []
        if queue:
            item = queue.pop(0)
            if isinstance(item, BaseException):
                raise item

    def phase(self, job_id: int, phase: str) -> dict[str, Any]:
        with self.lock:
            self.calls.append(("phase", self.session_id, job_id, phase))
        self._next("phase")
        return {}

    def log(self, job_id: int, data: bytes, offset: int | None = None) -> dict[str, Any]:
        with self.lock:
            self.calls.append(("log", self.session_id, job_id, offset, bytes(data)))
        self._next("log")
        got = self.received.setdefault(job_id, bytearray())
        assert offset is not None
        if offset > len(got):
            raise ClientError(409, "log gap", {"code": "log_gap", "expected_offset": len(got)})
        got.extend(data[len(got) - offset :])
        return {"job_id": job_id, "offset": len(got)}

    def upload_bundle(self, job_id: int, path: Path) -> dict[str, Any]:
        with self.lock:
            self.calls.append(("upload", self.session_id, job_id))
        self._next("upload")
        return {"bundle_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def finish(self, job_id, outcome, rc, summary=None, **extra) -> dict[str, Any]:
        with self.lock:
            self.calls.append(("finish", self.session_id, job_id, outcome, rc, extra))
        self._next("finish")
        return {"job_id": job_id, "state": outcome}

    def register(self, **kw: Any) -> dict[str, Any]:
        with self.lock:
            self.calls.append(("register", self.session_id, kw))
        self._next("register")
        return {"name": "w1", "pool": "default", "lanes": 1, "heartbeat_seconds": 5}

    def routes(self) -> list[str]:
        return [c[0] for c in self.calls]


def conn_fail() -> ClientError:
    return ClientError(0, "cannot reach http://fake: refused")


def http(status: int, code: str | None = None, **extra: Any) -> ClientError:
    body = {"error": f"HTTP {status}", **({"code": code} if code else {}), **extra}
    return ClientError(status, f"HTTP {status}", body)


@pytest.fixture
def fast(monkeypatch):
    """백오프 · 멈춤 창을 줄인다 — 시간 판정은 주입한 벽시계로 한다."""
    monkeypatch.setattr(rw, "BACKOFF_START_SECONDS", 0.001)
    monkeypatch.setattr(rw, "BACKOFF_MAX_SECONDS", 0.002)
    monkeypatch.setattr(rw, "LOG_FLUSH_SECONDS", 0.01)


def wworker(tmp_path: Path, fake: FakeServer, clock: WClock) -> rw.RemoteWorker:
    cfg = WorkerConfig(server="http://fake", token="t", data_dir=str(tmp_path / "wd"), lanes=1)
    logs: list[str] = []
    w = rw.RemoteWorker(cfg, client=fake, now_fn=clock.now, mono_fn=clock.mono_fn, log=logs.append)
    w.logs = logs  # type: ignore[attr-defined]
    w.config.data_path.mkdir(parents=True, exist_ok=True)
    return w


def ended_queue(
    w: rw.RemoteWorker, job_id: int, stream: bytes, *, acked: int = 0, **finish: Any
) -> rw.JobOutbox:
    """세션 폴더에 끝난 잡의 큐를 남긴다(보내기 전에 죽은 프로세스 흉내)."""
    d = w._job_dir(job_id)
    assert d is not None
    ob = rw.JobOutbox(d)
    ob.append(stream)
    ob.close()
    ob.acked = acked
    ob.finish = {
        "outcome": SUCCEEDED,
        "exit_code": 0,
        "summary": None,
        "group_gone": True,
        "log_bytes": len(stream),
        **finish,
    }
    ob.ended_at = w._epoch()
    ob.save()
    return ob


def die(w: rw.RemoteWorker) -> None:
    """kill -9 흉내 — 세션 폴더의 잠금을 놓는다(파일은 그대로)."""
    assert w._session_fd is not None
    os.close(w._session_fd)
    w._session_fd = None


def restart(tmp_path: Path, fake: FakeServer, clock: WClock) -> rw.RemoteWorker:
    w2 = wworker(tmp_path, fake, clock)
    w2.prepare_sessions()
    return w2


def test_rl14_a_b_an_older_outbox_snapshot_never_lands_after_a_newer_one(tmp_path, monkeypatch):
    """보내는 스레드의 저장(옛 스냅숏)이 파일을 쓰기 전에 멈춘 사이 레인이 finish 를 저장한다.
    옛 스냅숏이 나중에 이름을 바꾸면 디스크의 큐에서 finish 가 사라진다 — 그 틈의 kill -9 가
    finish 를 잃는다(P3 1라운드 LOW). 스냅숏을 만든 순서대로 써야 한다."""
    ob = rw.JobOutbox(tmp_path / "jobs" / "7")
    ob.append(b"line\n")
    real = rw._write_json
    inside, go = threading.Event(), threading.Event()
    first = threading.Lock()

    def slow_first(path: Path, data: dict[str, Any]) -> None:
        if first.acquire(blocking=False):  # 첫 저장(보내는 스레드)만 멈춘다
            inside.set()
            assert go.wait(10)
        real(path, data)

    monkeypatch.setattr(rw, "_write_json", slow_first)
    ob.acked = 5
    sender = threading.Thread(target=ob.save)
    sender.start()
    assert inside.wait(10)

    def lane_finish() -> None:
        ob.finish = {"outcome": SUCCEEDED, "exit_code": 0, "log_bytes": 5}
        ob.ended_at = 1.0
        ob.save()

    lane = threading.Thread(target=lane_finish)
    lane.start()
    lane.join(0.3)  # 레인의 저장이 먼저 끝나거나(결함) 줄을 선다
    go.set()
    sender.join(10)
    lane.join(10)
    on_disk = rw.JobOutbox.load(tmp_path / "jobs" / "7")
    assert on_disk is not None and on_disk.finish is not None, "an older snapshot replaced it"
    assert on_disk.acked == 5


def test_rl20_b_a_spool_that_cannot_be_read_backs_off_and_says_so_once(tmp_path, fast):
    """`stream.log` 를 못 읽으면 빈 조각을 보내 성공으로 세지 않는다 — 그러면 `acked` 는 그대로인
    채 쉬지 않고 돈다(P3 1라운드 LOW). 읽기 실패는 백오프로 다시 해 보고 로그는 한 줄이다. 다시
    읽히면 남은 로그와 finish 가 간다."""
    clock = WClock()
    fake = FakeServer(clock)
    w = wworker(tmp_path, fake, clock)
    w.prepare_sessions()
    ob = ended_queue(w, 7, b"one\ntwo\n")
    spool = w._job_dir(7) / rw.STREAM_FILE
    hidden = spool.with_name("hidden")
    spool.rename(hidden)  # 읽을 수 없다(OSError)
    s = rw._JobSender(w, 7, ob).start()
    try:
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline and sum(1 for x in w.logs if "cannot read" in x) == 0:
            time.sleep(0.01)
        time.sleep(0.2)
        assert [c for c in fake.calls if c[0] == "log"] == [], "an empty piece counted as sent"
        assert sum(1 for x in w.logs if "cannot read" in x) == 1, w.logs
        assert ob.acked == 0 and not ob.done
        hidden.rename(spool)
        s.thread.join(10)
        assert ob.done and bytes(fake.received[7]) == b"one\ntwo\n"
        assert fake.routes()[-1] == "finish"
    finally:
        s.halt.set()


def test_rl17_c_a_a_line_longer_than_a_piece_goes_whole_while_the_job_runs(
    tmp_path, fast, monkeypatch
):
    """한 줄은 늘 조각 하나에 든다(§4.5 · F34 · F35). 줄 끝이 256 KiB 안에 없으면 그 줄 끝까지
    (서버 본문 상한 4 MiB 안에서) 한 조각으로 보낸다 — 줄 가운데서 자르면 서버가 두 조각에 걸친
    비밀을 온전히 가리지 못한다(P3 1라운드 LOW). 줄 끝 없는 꼬리는 잡이 끝난 뒤에만 간다."""
    monkeypatch.setattr(rw, "LOG_BATCH_BYTES", 16)
    clock = WClock()
    fake = FakeServer(clock)
    w, ob, s = live_sender(tmp_path, fake, clock)
    long_line = b"x" * 40 + b"\n"
    ob.append(b"short\n" + long_line + b"tail-no-newline")
    s.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and ob.acked < 6 + len(long_line):
            time.sleep(0.01)
        time.sleep(0.1)
        pieces = [c[4] for c in fake.calls if c[0] == "log"]
        assert pieces == [b"short\n", long_line], pieces
        end_job(w, ob)
        s.wake.set()
        s.thread.join(10)
        pieces = [c[4] for c in fake.calls if c[0] == "log"]
        assert pieces == [b"short\n", long_line, b"tail-no-newline"], pieces
    finally:
        s.halt.set()


def test_rl17_c_b_the_piece_limit_is_the_servers_body_limit():
    from remote_ci_monitor.remote_workers import MAX_WORKER_LOG_BODY

    assert rw.LOG_PIECE_MAX_BYTES == MAX_WORKER_LOG_BODY


def test_rl46_c_a_dead_queue_answers_do_not_go_stale(tmp_path, fast, monkeypatch):
    """죽은 세션 D 의 큐 둘. 등록 대기 300초는 서버가 오류로 응답한 시간만 센다(결정 337).
    워커 하나의 깃발은 큐 사이에서 낡았다(P3 1라운드 LOW). 503 뒤 409 로 끝난 #7 의 옛 응답과,
    로그에서 503 을 받았다가 보낸 #8 의 옛 응답이 #8 의 finish 가 받는 연결 실패 시간까지 세게
    했다. 깃발은 큐마다 두고 성공에 내리며, 끝난 큐는 보지 않는다."""
    monkeypatch.setattr(rw, "BACKOFF_START_SECONDS", 0.01)
    monkeypatch.setattr(rw, "BACKOFF_MAX_SECONDS", 0.01)
    clock = WClock()
    fake = FakeServer(clock)
    w1 = wworker(tmp_path, fake, clock)
    w1.prepare_sessions()
    ended_queue(w1, 7, b"seven\n")
    ended_queue(w1, 8, b"eight\n")
    die(w1)
    seven_over = threading.Event()
    state = {"finish_7": 0, "log_8": 0, "slow_8": 3}
    real_log, real_finish = fake.log, fake.finish

    def log(job_id: int, data: bytes, offset: int | None = None) -> dict[str, Any]:
        if job_id == 8 and state["log_8"] == 0:
            state["log_8"] += 1
            raise http(503)
        return real_log(job_id, data, offset)

    def finish(job_id, outcome, rc, summary=None, **extra) -> dict[str, Any]:
        if job_id == 7:
            state["finish_7"] += 1
            if state["finish_7"] == 1:
                raise http(503)
            seven_over.set()
            raise http(409, "job_closed", state=LOST)  # #7 은 503 을 받은 채 끝난다
        if job_id == 8 and state["slow_8"] > 0:
            assert seven_over.wait(10)
            time.sleep(0.1)  # #7 의 스레드가 끝난다
            state["slow_8"] -= 1
            clock.advance(400)  # 연결 실패 하나가 400초 걸린다
            time.sleep(0.2)
            raise conn_fail()
        return real_finish(job_id, outcome, rc, summary, **extra)

    fake.log = log  # type: ignore[method-assign]
    fake.finish = finish  # type: ignore[method-assign]
    w2 = restart(tmp_path, fake, clock)
    w2.send_dead_queues()
    assert not any("queue not delivered" in s for s in w2.logs), w2.logs
    assert [c[2] for c in fake.calls if c[0] == "finish"] == [8], fake.calls
    assert bytes(fake.received[8]) == b"eight\n"


def test_rl14_a_a_finish_left_on_disk_is_sent_with_the_old_session_before_registering(
    tmp_path, fast
):
    clock = WClock()
    fake = FakeServer(clock)
    w1 = wworker(tmp_path, fake, clock)
    w1.prepare_sessions()
    old = w1.session_id
    ended_queue(w1, 7, b"one\ntwo\n")
    die(w1)
    w2 = restart(tmp_path, fake, clock)
    w2.send_dead_queues()
    w2.register()
    routes = [(c[0], c[1]) for c in fake.calls]
    assert routes.index(("finish", old)) < routes.index(("register", fake.session_id)), routes
    fin_call = next(c for c in fake.calls if c[0] == "finish")
    assert fin_call[3] == SUCCEEDED and fin_call[5]["log_bytes"] == 8
    assert bytes(fake.received[7]) == b"one\ntwo\n"


def test_rl16_b_a_log_gap_makes_the_worker_resend_from_the_expected_offset(tmp_path, fast):
    """RL16 (b): 요청이 유실돼 워커의 다음 조각 오프셋이 서버보다 앞선다(흉내 — 디스크의 `acked`
    가 서버보다 크다). 서버의 409 `log_gap` 이 준 `expected_offset` 부터 다시 보내 서버 로그가
    스트림과 바이트로 같고, 프로세스는 멈추지 않는다(§4.5 응답 표 · 결정 271)."""
    clock = WClock()
    fake = FakeServer(clock)
    w, ob, s = live_sender(tmp_path, fake, clock)
    stream = b"one\ntwo\nthree\n"
    ob.append(stream)
    fake.received[7] = bytearray(stream[:4])  # 서버는 4 바이트만 받았다
    ob.acked = 8  # 워커는 8 까지 받았다고 안다
    end_job(w, ob)
    s.start()
    s.thread.join(3)
    alive = s.thread.is_alive()
    s.halt.set()  # 변이에서 끝없이 도는 스레드를 멈춘다
    assert not alive, fake.calls[:6]
    assert bytes(fake.received[7]) == stream
    offsets = [c[3] for c in fake.calls if c[0] == "log"]
    assert offsets[:2] == [8, 4], offsets  # 앞선 조각 → 409 → 4 부터
    assert not w.stopping.is_set()
    assert [c[3] for c in fake.calls if c[0] == "finish"] == [SUCCEEDED]


def test_rl16_d_a_restarted_worker_asks_the_offset_and_sends_only_the_rest(tmp_path, fast):
    clock = WClock()
    fake = FakeServer(clock)
    w1 = wworker(tmp_path, fake, clock)
    w1.prepare_sessions()
    stream = b"".join(f"line {i}\n".encode() for i in range(400))
    fake.received[7] = bytearray(stream[:1000])  # 서버는 1000 바이트를 받았다
    ended_queue(w1, 7, stream, acked=900)  # 디스크의 acked 는 하한일 뿐이다
    die(w1)
    w2 = restart(tmp_path, fake, clock)
    w2.send_dead_queues()
    logs = [c for c in fake.calls if c[0] == "log"]
    assert logs[0][3:] == (900, b"")  # 첫 로그 요청은 빈 본문 오프셋 질의
    assert sum(len(c[4]) for c in logs) == len(stream) - 1000  # 0 부터 다시 보내지 않는다
    assert bytes(fake.received[7]) == stream


def test_rl39_a_dead_queue_fenced_sends_only_the_narrow_confirm_and_keeps_running(tmp_path, fast):
    clock = WClock()
    fake = FakeServer(clock)
    w1 = wworker(tmp_path, fake, clock)
    w1.prepare_sessions()
    ended_queue(w1, 7, b"x\n")
    die(w1)
    fake.script["log"] = [http(409, "session_fenced")]
    w2 = restart(tmp_path, fake, clock)
    w2.send_dead_queues()
    finishes = [c for c in fake.calls if c[0] == "finish"]
    assert len(finishes) == 1, finishes
    assert finishes[0][3] == LOST and finishes[0][5]["summary_code"] == "worker_replaced"
    assert finishes[0][5]["group_gone"] is True
    assert not w2.fenced.is_set() and not w2.stopping.is_set()  # W5 를 부르지 않았다


def test_rl39_b_dead_queue_not_registered_drops_it_and_never_registers_the_dead_id(tmp_path, fast):
    clock = WClock()
    fake = FakeServer(clock)
    w1 = wworker(tmp_path, fake, clock)
    w1.prepare_sessions()
    old = w1.session_id
    ended_queue(w1, 7, b"x\n")
    ended_queue(w1, 8, b"y\n")
    die(w1)
    # 서버가 D 를 모르면 D 의 모든 요청이 409 다 — 하나만 409 로 두면 두 큐의 보내는 스레드가
    # 겨뤄 다른 잡의 finish 가 먼저 나가는 일이 있었다(batch-2 에서 6번 중 1번 빨강)
    fake.script["log"] = [http(409, "not_registered")] * 10
    w2 = restart(tmp_path, fake, clock)
    w2.send_dead_queues()
    w2.register()
    assert [c for c in fake.calls if c[0] == "register" and c[1] == old] == []
    assert not [c for c in fake.calls if c[0] == "finish"]
    assert any("dropping its queue" in s for s in w2.logs)


def test_rl39_c_dead_queue_unauthorized_keeps_the_files_byte_for_byte(tmp_path, fast):
    clock = WClock()
    fake = FakeServer(clock)
    w1 = wworker(tmp_path, fake, clock)
    w1.prepare_sessions()
    ended_queue(w1, 7, b"secret result\n")
    d = w1._job_dir(7)
    die(w1)
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in d.iterdir()}
    fake.script["log"] = [http(401)]
    w2 = restart(tmp_path, fake, clock)
    w2.send_dead_queues()
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in d.iterdir()}
    assert after == before
    assert not [c for c in fake.calls if c[0] == "finish"]


def test_rl46_a_dead_queue_with_server_errors_registers_after_300s_answered(tmp_path, fast):
    clock = WClock()
    fake = FakeServer(clock, tick=40)
    w1 = wworker(tmp_path, fake, clock)
    w1.prepare_sessions()
    ended_queue(w1, 7, b"x\n")
    die(w1)
    fake.script["log"] = [http(503, "log_write_failed")] * 1000
    fake.script["finish"] = [http(500)] * 1000
    w2 = restart(tmp_path, fake, clock)
    started = time.monotonic()
    w2.send_dead_queues()
    assert time.monotonic() - started < 15
    assert any("queue not delivered after 300s" in s and "#7" in s for s in w2.logs), w2.logs
    assert not [c for c in fake.calls if c[0] == "register"]  # 기다리는 동안 등록 0
    w2.register()  # 그 뒤 정상 등록


def test_rl46_b_connection_failures_do_not_count_toward_the_300s(tmp_path, fast, monkeypatch):
    # 백오프를 늘려 실패 다섯 번이 0.5초에 걸치게 한다 — 대기 고리(0.05초 박자)가 그 사이를 여러
    # 번 본다. 너무 빨리 끝나면 연결 실패를 세는 변이(dead-session-counts-connection-failures)도
    # 초록이었다(batch-2 에서 찾음)
    monkeypatch.setattr(rw, "BACKOFF_START_SECONDS", 0.1)
    monkeypatch.setattr(rw, "BACKOFF_MAX_SECONDS", 0.1)
    clock = WClock()
    fake = FakeServer(clock, tick=400)  # 연결 실패 한 번에 400초가 흐른다
    w1 = wworker(tmp_path, fake, clock)
    w1.prepare_sessions()
    ended_queue(w1, 7, b"x\n")
    die(w1)
    fake.script["log"] = [conn_fail()] * 5
    w2 = restart(tmp_path, fake, clock)
    w2.send_dead_queues()
    assert not any("queue not delivered" in s for s in w2.logs), w2.logs
    assert [c[3] for c in fake.calls if c[0] == "finish"] == [SUCCEEDED]


def live_sender(tmp_path: Path, fake: FakeServer, clock: WClock, job_id: int = 7):
    w = wworker(tmp_path, fake, clock)
    ob = rw.JobOutbox(None)
    s = rw._JobSender(w, job_id, ob)
    return w, ob, s


def end_job(w: rw.RemoteWorker, ob: rw.JobOutbox, **extra: Any) -> None:
    ob.finish = {"outcome": SUCCEEDED, "exit_code": 0, "log_bytes": ob.length, **extra}
    ob.ended_at = w._epoch()


def test_rl40_finish_goes_first_after_300s_of_5xx_on_the_log(tmp_path, fast):
    clock = WClock()
    fake = FakeServer(clock, tick=60)
    w, ob, s = live_sender(tmp_path, fake, clock)
    ob.append(b"a\n" * 10)
    ob.bundle = {"path": str(tmp_path / "nope.tar"), "state": "pending", "sha256": None}
    end_job(w, ob, artifacts={"state": "ready", "file_count": 1, "total_bytes": 2})
    fake.script["log"] = [http(503, "log_write_failed")] * 1000
    s.start()
    s.thread.join(10)
    assert ob.done
    fins = [c for c in fake.calls if c[0] == "finish"]
    assert len(fins) == 1 and fins[0][5]["log_bytes"] == 20
    art_sent = fins[0][5]["artifacts"]
    assert (art_sent["state"], art_sent["reason_code"]) == ("failed", "upload_failed")
    assert timedelta(seconds=300) <= clock.wall - T0W < timedelta(hours=1)  # 24시간이 아니다
    after = fake.calls[fake.calls.index(fins[0]) + 1 :]
    assert after == []  # 남은 로그 · 산출물은 보내지 않는다


def test_rl40_connection_failures_keep_the_order_and_send_the_log_first(tmp_path, fast):
    clock = WClock()
    fake = FakeServer(clock, tick=100)
    w, ob, s = live_sender(tmp_path, fake, clock)
    ob.append(b"a\n" * 10)
    end_job(w, ob)
    fake.script["log"] = [conn_fail()] * 8  # 800초 — 연결 실패는 finish 를 앞당기지 않는다
    s.start()
    s.thread.join(10)
    routes = fake.routes()
    assert routes.index("finish") > max(i for i, r in enumerate(routes) if r == "log")
    assert bytes(fake.received[7]) == b"a\n" * 10


def test_rl19_b_after_24h_the_finish_goes_without_the_log(tmp_path, fast):
    clock = WClock()
    fake = FakeServer(clock, tick=3 * 3600)
    w, ob, s = live_sender(tmp_path, fake, clock)
    ob.append(b"a\n")
    end_job(w, ob)
    fake.script["log"] = [conn_fail()] * 1000
    s.start()
    s.thread.join(10)
    fins = [c for c in fake.calls if c[0] == "finish"]
    assert len(fins) == 1 and fins[0][5]["log_bytes"] == 2
    assert any("not delivered in 24h" in x for x in w.logs)


def test_rl19_b_a_finish_not_sent_in_24h_is_dropped_with_one_line(tmp_path, fast):
    """§4.5 「재전송 창」: finish 마저 24시간 안에 못 보내면 큐 항목을 버리고 로그 한 줄을 남긴다
    (§4.1 타이머 표 「finish 도 못 보냈으면 큐 항목 버림 + 로그」, 결정 264). 보내는 스레드가
    끝나야 레인이 풀리고 폴더가 끝없이 다음 프로세스로 넘어가지 않는다."""
    clock = WClock()
    fake = FakeServer(clock, tick=3 * 3600)
    w, ob, s = live_sender(tmp_path, fake, clock)
    ob.append(b"a\n")
    end_job(w, ob)
    fake.script["log"] = [conn_fail()] * 1000
    fake.script["finish"] = [conn_fail()] * 1000
    s.start()
    s.thread.join(3)
    assert not s.thread.is_alive(), "the sender still retries the finish after 24h"
    assert ob.done
    fins = [c for c in fake.calls if c[0] == "finish"]
    assert len(fins) == 1  # 24시간 뒤 log_bytes 를 실은 finish 한 번 — 그것도 못 가면 버린다
    dropped = [x for x in w.logs if "finish not delivered in 24h" in x]
    assert dropped == ["#7 report: finish not delivered in 24h — dropping the report"], w.logs


def test_rl19_a_a_running_job_never_gives_up_on_5xx(tmp_path, fast):
    clock = WClock()
    fake = FakeServer(clock, tick=10)
    w, ob, s = live_sender(tmp_path, fake, clock)
    fake.script["log"] = [http(503)] * 40 + [conn_fail()] * 30  # 분단 5분 넘게
    s.start()
    for i in range(50):
        ob.append(f"line {i}\n".encode())
        s.wake.set()
        time.sleep(0.002)
    deadline = time.monotonic() + 10
    while ob.pending() and time.monotonic() < deadline:
        time.sleep(0.01)
    end_job(w, ob)
    s.wake.set()
    s.thread.join(10)
    assert bytes(fake.received[7]) == b"".join(f"line {i}\n".encode() for i in range(50))
    assert [c[3] for c in fake.calls if c[0] == "finish"] == [SUCCEEDED]


def test_rl20_twelve_mib_during_a_partition_all_arrive(tmp_path, fast):
    clock = WClock()
    fake = FakeServer(clock)
    w, ob, s = live_sender(tmp_path, fake, clock)
    fake.script["log"] = [conn_fail()] * 20
    line = b"x" * 1023 + b"\n"
    for _ in range(12 * 1024):
        ob.append(line)
    end_job(w, ob)
    s.start()
    s.thread.join(30)
    got = bytes(fake.received[7])
    assert len(got) == 12 * 1024 * 1024 and got == line * (12 * 1024)
    sizes = [len(c[4]) for c in fake.calls if c[0] == "log"]
    assert max(sizes) <= rw.LOG_BATCH_BYTES  # RL17 c — 조각은 256 KiB 이하


def test_rl20_a_broken_spool_counts_bytes_so_the_server_marks_the_log_incomplete(
    tmp_path, fast, monkeypatch
):
    clock = WClock()
    fake = FakeServer(clock)
    w = wworker(tmp_path, fake, clock)
    d = tmp_path / "jobdir"
    ob = rw.JobOutbox(d)
    ob.append(b"first\n")
    ob.close()

    def full(*a: Any, **kw: Any):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "open", full)
    ob.append(b"second\n")
    monkeypatch.undo()
    assert ob.broken and ob.length == 13 and ob.written == 6
    s = rw._JobSender(w, 7, ob)
    end_job(w, ob)
    s.start()
    s.thread.join(10)
    fin = next(c for c in fake.calls if c[0] == "finish")
    assert fin[5]["log_bytes"] == 13 and bytes(fake.received[7]) == b"first\n"


def test_rl17_c_chunks_end_at_a_line_end(tmp_path, fast):
    clock = WClock()
    fake = FakeServer(clock)
    w, ob, s = live_sender(tmp_path, fake, clock)
    for i in range(3000):
        ob.append(b"y" * (200 + i % 50) + b"\n")
    end_job(w, ob)
    s.start()
    s.thread.join(10)
    chunks = [c[4] for c in fake.calls if c[0] == "log"]
    assert len(chunks) > 1 and all(ch.endswith(b"\n") for ch in chunks)
    assert all(len(ch) <= rw.LOG_BATCH_BYTES for ch in chunks)


def test_rl21_a_a_lost_finish_answer_then_409_job_closed_is_delivered(tmp_path, fast):
    clock = WClock()
    fake = FakeServer(clock)
    w, ob, s = live_sender(tmp_path, fake, clock)
    end_job(w, ob)
    closed = http(409, "job_closed", state=SUCCEEDED)
    fake.script["finish"] = [conn_fail(), closed]
    s.start()
    s.thread.join(10)
    assert ob.done and not s.alive()
    assert len([c for c in fake.calls if c[0] == "finish"]) == 2  # 반복하지 않는다


def test_rl22_a_the_upload_is_retried_and_the_finish_follows_it(tmp_path, fast):
    clock = WClock()
    fake = FakeServer(clock)
    w, ob, s = live_sender(tmp_path, fake, clock)
    tar = tmp_path / "b.tar"
    tar.write_bytes(b"bundle")
    ob.bundle = {"path": str(tar), "state": "pending", "sha256": None}
    end_job(w, ob, artifacts={"state": "ready", "file_count": 1, "total_bytes": 6})
    fake.script["upload"] = [conn_fail(), http(503)]
    s.start()
    s.thread.join(10)
    assert fake.routes() == ["upload", "upload", "upload", "finish"]
    sent = fake.calls[-1][5]["artifacts"]
    assert sent["state"] == "ready"
    assert sent["bundle_sha256"] == hashlib.sha256(b"bundle").hexdigest()


def test_rl22_c_restart_between_upload_and_finish_reuses_the_kept_bundle(tmp_path, fast):
    clock = WClock()
    fake = FakeServer(clock)
    w1 = wworker(tmp_path, fake, clock)
    w1.prepare_sessions()
    tar = tmp_path / "kept.tar"
    tar.write_bytes(b"kept bundle")
    ob = ended_queue(w1, 7, b"x\n", artifacts={"state": "ready", "file_count": 1})
    ob.bundle = {"path": str(tar), "state": "pending", "sha256": None}
    ob.save()
    die(w1)
    w2 = restart(tmp_path, fake, clock)
    w2.send_dead_queues()
    assert [r for r in fake.routes() if r in ("upload", "finish")] == ["upload", "finish"]
    assert (
        fake.calls[-1][5]["artifacts"]["bundle_sha256"]
        == hashlib.sha256(b"kept bundle").hexdigest()
    )


def test_rl12_b_drain_gives_up_after_the_limit_and_leaves_the_queue_on_disk(
    tmp_path, fast, monkeypatch
):
    monkeypatch.setattr(rw, "DRAIN_REPORT_SECONDS", 0.3)
    clock = WClock()
    fake = FakeServer(clock)
    w = wworker(tmp_path, fake, clock)
    w.prepare_sessions()
    fake.script["finish"] = [conn_fail()] * 100000
    ob = rw.JobOutbox(w._job_dir(7))
    s = rw._JobSender(w, 7, ob).start()
    w._senders[7] = s
    with w._lock:
        w.running[7] = 1
    w.draining.set()
    lane = threading.Thread(
        target=w._finish, args=(7, SUCCEEDED, 0, None, None), kwargs={"group_gone": True}
    )
    lane.daemon = True
    lane.start()
    lane.join(10)
    assert not lane.is_alive(), "the lane still waits for its queue past DRAIN_REPORT_SECONDS"
    assert 7 in w.running  # 큐가 안 끝난 잡은 heartbeat 목록에 남는다(rl12_b_b_a)
    assert (w._job_dir(7) / "outbox.json").is_file()  # 새 이미지가 등록 전에 보낸다
    assert any("restarting anyway" in x for x in w.logs)
    s.halt.set()


def test_rl12_b_b_a_queue_past_the_drain_limit_stays_in_the_heartbeat_until_it_is_sent(
    tmp_path, fast, monkeypatch
):
    """드레인 한도(60초)가 지나 레인은 풀려도 그 잡의 큐는 끝나지 않았다. heartbeat 의 `jobs` 에는
    큐가 안 끝난 잡이 남는다(§4.5 「레인은 자기 잡의 큐가 끝날 때까지」 · F10). 빼면 exec 전에
    도는 heartbeat 하나로 서버가 그 잡을 「잊힌 잡」(T5, `worker_restarted_without_job`)으로
    닫는다 — rcm-ci 4341 의 RL12 b e2e 실패가 그것이었다. 보내는 스레드는 exec 까지 계속
    보내고, 보낸 뒤에야 목록에서 빠지고 폴더가 지워진다(드레인이 취소돼도 결과를 잃지 않는다)."""
    monkeypatch.setattr(rw, "DRAIN_REPORT_SECONDS", 0.3)
    clock = WClock()
    fake = FakeServer(clock)
    w = wworker(tmp_path, fake, clock)
    w.prepare_sessions()
    fake.script["finish"] = [http(503)] * 100000
    ob = rw.JobOutbox(w._job_dir(7))
    s = rw._JobSender(w, 7, ob).start()
    w._senders[7] = s
    with w._lock:
        w.running[7] = 1
    w.draining.set()
    lane = threading.Thread(
        target=w._finish, args=(7, SUCCEEDED, 0, None, None), kwargs={"group_gone": True}
    )
    lane.daemon = True
    lane.start()
    lane.join(10)
    assert not lane.is_alive()
    sent: list[list[int]] = []

    def heartbeat_call(jobs: list[int], seq: int, settled: int) -> dict[str, Any]:
        sent.append(list(jobs))
        return {}

    w._heartbeat_call = heartbeat_call  # type: ignore[method-assign]
    w.heartbeat_once()
    assert sent == [[7]], "the heartbeat dropped a job whose report is still queued"
    try:
        fake.script["finish"].clear()  # 서버가 돌아왔다(또는 드레인이 취소됐다)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and (7 in w.running or w._job_dir(7).exists()):
            time.sleep(0.01)
        assert [c[3] for c in fake.calls if c[0] == "finish"][-1] == SUCCEEDED
        assert 7 not in w.running and 7 not in w._senders
        assert not w._job_dir(7).exists()  # 보냈다 — 다음 프로세스가 판정할 것이 없다
    finally:
        s.halt.set()


def test_rl23_snapshot_download_cut_off_is_lost_snapshot_unreachable(tmp_path, fast):
    clock = WClock()
    fake = FakeServer(clock)
    w = wworker(tmp_path, fake, clock)

    def no_server(job_id: int, dest: Path) -> int:
        raise conn_fail()

    fake.download_tree = no_server  # type: ignore[attr-defined]
    job = {
        "job": {"id": 7, "preset": "ok", "state": RUNNING, "source": {"mode": "tree"}},
        "preset": {"name": "ok", "argv": ["sh", "-c", "echo hi"]},
    }
    w.run_claimed(1, job)
    fin = next(c for c in fake.calls if c[0] == "finish")
    assert fin[3] == LOST and fin[5]["summary_code"] == "snapshot_unreachable", fin
    assert not list((w.config.data_path / "jobs").rglob("*.part"))


def test_rl23_a_missing_snapshot_is_still_failed(tmp_path, fast):
    clock = WClock()
    fake = FakeServer(clock)
    w = wworker(tmp_path, fake, clock)

    def gone(job_id: int, dest: Path) -> int:
        raise http(404)

    fake.download_tree = gone  # type: ignore[attr-defined]
    job = {
        "job": {"id": 7, "preset": "ok", "state": RUNNING, "source": {"mode": "tree"}},
        "preset": {"name": "ok", "argv": ["sh", "-c", "echo hi"]},
    }
    w.run_claimed(1, job)
    fin = next(c for c in fake.calls if c[0] == "finish")
    assert fin[3] == "failed" and fin[5]["summary_code"] == "snapshot_download_failed", fin


# ── P3 2라운드 ─────────────────────────────────────────────────────────────────


def wait_for(pred: Any, seconds: float = 10.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return bool(pred())


def test_rl40_b_finished_job_whose_spool_cannot_be_read_sends_the_finish_after_300s(
    srv, tmp_path, fast
):
    """끝난 잡의 `stream.log` 를 못 읽으면 5xx 와 같은 300초 규칙으로 finish 가 먼저 간다(결정
    324 · §4.5). 못 읽는 스풀이 finish 를 24시간 묶으면 서버는 잡을 `running` 으로 두고 레인도
    잡혀 있다(P3 2라운드 MED). 잡은 진짜 결과로 닫히고, 받지 못한 바이트가 있으니 `log_complete`
    는 false 다. 로그 줄은 finish 를 먼저 보낸다고 말한다."""
    from remote_ci_monitor.client import WorkerClient

    jid = running(srv)
    assert post_log(srv, jid, b"one\n", 0)[0] == 200
    clock = WClock()
    client = WorkerClient(f"http://127.0.0.1:{srv.port}", srv.tokens[W])
    w = wworker(tmp_path, client, clock)  # type: ignore[arg-type]
    w.session_id = srv.session(W)
    w._bind_session(w.client)
    w.session_dir = tmp_path / "wd" / rw.SESSIONS_DIR / w.session_id
    ob = ended_queue(w, jid, b"one\ntwo\n", acked=4)
    spool = w._job_dir(jid) / rw.STREAM_FILE
    spool.rename(spool.with_name("hidden"))  # 읽을 수 없다(OSError)
    s = rw._JobSender(w, jid, ob).start()
    try:
        assert wait_for(lambda: any("cannot read" in x for x in w.logs)), w.logs
        time.sleep(0.1)
        assert srv.store.get_job(jid).state == RUNNING  # 300초 전 — 아직 로그가 앞이다
        clock.advance(rw.FINISH_HOLD_SECONDS + 1)
        s.wake.set()
        s.thread.join(10)
        assert ob.done, "the finish waits behind an unreadable spool"
    finally:
        s.halt.set()
    assert srv.store.get_job(jid).state == SUCCEEDED
    doc = job_doc(srv, jid)
    assert doc["state"] == SUCCEEDED and doc["log_complete"] is False
    assert any("sending the finish first" in x for x in w.logs), w.logs


@pytest.mark.parametrize("keep", [0, 6])
def test_rl20_b_b_spool_shorter_than_written_is_unreadable_not_an_empty_piece(tmp_path, fast, keep):
    """스풀 파일이 `written` 보다 짧다(잘렸다). 짧은 읽기 · EOF 너머의 `b""` 도 못 읽음이다 — 빈
    조각이나 앞부분만 보내 2xx 로 성공하면 `acked` 가 그대로인 채 쉬지 않고 돈다(P3 2라운드
    LOW)."""
    clock = WClock()
    fake = FakeServer(clock)
    w = wworker(tmp_path, fake, clock)
    w.prepare_sessions()
    ob = ended_queue(w, 7, b"one\ntwo\n")
    spool = w._job_dir(7) / rw.STREAM_FILE
    with spool.open("r+b") as fh:
        fh.truncate(keep)
    s = rw._JobSender(w, 7, ob).start()
    try:
        assert wait_for(lambda: any("cannot read" in x for x in w.logs), 2), w.logs
        time.sleep(0.2)
        logs = [c for c in fake.calls if c[0] == "log"]
        assert logs == [], f"a short read was sent as a piece: {len(logs)} log calls"
        assert sum(1 for x in w.logs if "cannot read" in x) == 1, w.logs
        assert ob.acked == 0 and not ob.done
    finally:
        s.halt.set()


def test_rl17_c_d_a_job_that_ends_while_its_piece_is_read_is_not_cut_mid_line(tmp_path, fast):
    """조각을 읽은 **뒤에** 잡이 끝났는지를 보면, 그 사이 줄 끝이 이어 쓰이고 finish 가 적힌 잡의
    줄 끝 없는 조각을 보낸다 — 다음 조각이 줄 가운데서 시작한다(P3 2라운드 LOW). 끝났는지는 읽기
    전에 본다."""
    clock = WClock()
    fake = FakeServer(clock)
    w, ob, s = live_sender(tmp_path, fake, clock)
    ob.append(b"abc")
    real = ob.read
    raced: list[bool] = []

    def read_then_the_job_ends(start: int, limit: int) -> bytes | None:
        data = real(start, limit)
        if not raced:
            raced.append(True)
            ob.append(b"def\n")  # 읽은 뒤 · finish 를 보기 전에 줄 끝이 오고 잡이 끝난다
            end_job(w, ob)
        return data

    ob.read = read_then_the_job_ends  # type: ignore[method-assign]
    assert s._chunk() == b"", "a piece of a running job was sent without its line end"
    assert s._chunk() == b"abcdef\n"


def test_rl17_c_e_a_line_longer_than_the_piece_limit_is_cut_at_the_limit(
    tmp_path, fast, monkeypatch
):
    """줄 끝이 `LOG_PIECE_MAX_BYTES`(서버 본문 상한) 안에 없으면 그 상한에서 자른다 — 도는 잡이라도
    그 줄을 기다리지 않고, 상한보다 긴 조각을 만들지 않는다(서버가 413 으로 거절한다)."""
    monkeypatch.setattr(rw, "LOG_BATCH_BYTES", 16)
    monkeypatch.setattr(rw, "LOG_PIECE_MAX_BYTES", 32)
    clock = WClock()
    fake = FakeServer(clock)
    w, ob, s = live_sender(tmp_path, fake, clock)
    ob.append(b"x" * 50 + b"\n")
    s.start()
    try:
        assert wait_for(lambda: ob.acked == 51, 5), [c[4] for c in fake.calls if c[0] == "log"]
        pieces = [c[4] for c in fake.calls if c[0] == "log"]
        assert pieces == [b"x" * 32, b"x" * 18 + b"\n"], pieces
        assert ob.finish is None  # 잡은 아직 돈다
    finally:
        s.halt.set()


class LaneFake(FakeServer):
    """claim 도 받는다. 서버에서 잡 #7 이 그 레인을 잡고 있는 동안(finish 가 아직 안 갔다) 같은
    세션의 claim 은 서버처럼 409 `lane 1 already has job #7` 이다(remote_workers.py 의 자기 세션
    `LaneBusy`)."""

    def claim(self, lane: int, wait: int, claim_seq: int | None = None) -> Any:
        with self.lock:
            self.calls.append(("claim", self.session_id, lane))
            closed = self.closed
        if not closed:
            raise ClientError(409, f"lane {lane} already has job #7", {"error": "busy"})
        return None

    def finish(self, job_id, outcome, rc, summary=None, **extra) -> dict[str, Any]:
        out = super().finish(job_id, outcome, rc, summary, **extra)
        self.closed = True
        return out

    closed = False


def test_rl12_b_c_a_lane_freed_by_the_drain_limit_waits_for_its_queue_after_a_cancel(
    tmp_path, fast, monkeypatch
):
    """드레인 한도(60초) 뒤 레인은 풀리지만 그 잡의 큐는 아직 보낸다. 드레인이 취소되면 같은 레인이
    다시 claim 하는데 서버에서는 옛 잡이 그 레인을 잡고 있어 409 `LaneBusy` 가 2~4초마다 온다(P3
    2라운드 LOW). 그 레인은 큐가 끝날 때까지 claim 하지 않고, 끝나면 claim 한다."""
    monkeypatch.setattr(rw, "DRAIN_REPORT_SECONDS", 0.3)
    monkeypatch.setattr(rw, "RETRY_WAIT_SECONDS", 0.01)
    monkeypatch.setattr(rw, "CLAIM_MIN_INTERVAL", 0.01)
    monkeypatch.setattr(rw, "DRAIN_WAIT_SECONDS", 0.01)
    clock = WClock()
    fake = LaneFake(clock)
    w = wworker(tmp_path, fake, clock)
    w.prepare_sessions()
    fake.script["finish"] = [http(503)] * 100000
    ob = rw.JobOutbox(w._job_dir(7))
    s = rw._JobSender(w, 7, ob).start()
    w._senders[7] = s
    with w._lock:
        w.running[7] = 1
    w.draining.set()
    w._finish(7, SUCCEEDED, 0, None, None, group_gone=True)  # 한도 뒤에 돌아온다
    assert 7 in w.running and s.alive()
    w.draining.clear()  # 서버가 지금 버전으로 돌아왔다 — 드레인 취소
    lane = threading.Thread(target=w._lane_loop, args=(1,), daemon=True)
    lane.start()
    try:
        time.sleep(0.3)
        claims = [c for c in fake.calls if c[0] == "claim"]
        assert claims == [], f"the lane claimed over its open queue: {w.logs[-3:]}"
        fake.script["finish"].clear()  # 큐가 끝난다
        assert wait_for(lambda: any(c[0] == "claim" for c in fake.calls), 5)
        assert 7 not in w.running
        assert not any("already has job" in x for x in w.logs), w.logs
    finally:
        w.stopping.set()
        s.halt.set()
        lane.join(5)


@pytest.mark.parametrize(
    "refusal", [RuntimeError("boom"), http(409, "protocol_required")], ids=["error", "protocol"]
)
def test_rl14_a_c_a_queue_that_ends_unsent_while_the_worker_runs_stays_in_the_heartbeat(
    tmp_path, fast, refusal, monkeypatch
):
    """보내는 스레드가 `done` 없이 끝났다(예상 못 한 예외 · `protocol_required`) — 워커는 멈추지
    않고 계속 돈다. 그 잡을 heartbeat 목록에서 빼면 finish 가 디스크에만 있는데 서버가 그 잡을
    「잊힌 잡」(T5, `worker_restarted_without_job`)으로 닫는다(P3 2라운드 LOW). 잡은 목록에 남고
    폴더도 남는다 — 다음 프로세스가 디스크에서 보낸다. 멈춤 · 드레인 exec 은 p63_1 이 본다.

    P6-1 G4 뒤로 한 단계(attempt) 안의 예상 못 한 예외는 다시 해 본다(p6_1_g4_d). 스레드를
    끝내는 예외는 단계 밖(고르기)에서 난 것이라, [error] 는 거기서 던진다."""
    clock = WClock()
    fake = FakeServer(clock)
    w = wworker(tmp_path, fake, clock)
    w.prepare_sessions()
    ob = rw.JobOutbox(w._job_dir(7))
    s = rw._JobSender(w, 7, ob)
    if isinstance(refusal, RuntimeError):

        def broken_pick() -> bool:
            raise refusal

        monkeypatch.setattr(s, "_finish_first", broken_pick)
    else:
        fake.script["finish"] = [refusal]
    s.start()
    w._senders[7] = s
    with w._lock:
        w.running[7] = 1
    w._finish(7, SUCCEEDED, 0, None, None, group_gone=True)
    s.thread.join(10)
    assert not s.alive() and not ob.done
    sent: list[list[int]] = []

    def heartbeat_call(jobs: list[int], seq: int, settled: int) -> dict[str, Any]:
        sent.append(list(jobs))
        return {}

    w._heartbeat_call = heartbeat_call  # type: ignore[method-assign]
    w.heartbeat_once()
    assert sent == [[7]], "the heartbeat dropped a job whose finish is only on disk"
    on_disk = rw.JobOutbox.load(w._job_dir(7))
    assert on_disk is not None and on_disk.finish is not None and not on_disk.done


def test_rl17_c_f_a_running_job_sends_every_whole_line_up_to_the_last_line_end(
    tmp_path, fast, monkeypatch
):
    """도는 잡의 조각은 `acked` 부터 마지막 줄 끝까지다(F34). 줄 끝 없는 꼬리만 남기고, 앞의 줄
    여럿을 한 조각으로 보낸다 — 마지막 줄 끝을 못 보면 짧은 줄은 잡이 끝날 때까지 안 가거나
    줄마다 한 조각이 된다."""
    monkeypatch.setattr(rw, "LOG_BATCH_BYTES", 16)
    clock = WClock()
    fake = FakeServer(clock)
    w, ob, s = live_sender(tmp_path, fake, clock)
    ob.append(b"aaa\nbbb\nccc\nddd")
    s.start()
    try:
        assert wait_for(lambda: ob.acked == 12, 5), [c[4] for c in fake.calls if c[0] == "log"]
        time.sleep(0.1)
        pieces = [c[4] for c in fake.calls if c[0] == "log"]
        assert pieces == [b"aaa\nbbb\nccc\n"], pieces
        assert ob.finish is None
    finally:
        s.halt.set()


# ── P6-1: P5 엣지 감사의 확인된 GAP ────────────────────────────────────────────


def _dead_job_with(tmp_path: Path, fake: FakeServer, clock: WClock, how: str):
    """끝난 잡의 큐를 남기고 죽은 프로세스 — 그 `outbox.json` 을 `how` 대로 망가뜨린다."""
    w1 = wworker(tmp_path, fake, clock)
    w1.prepare_sessions()
    ended_queue(w1, 7, b"one\ntwo\n")
    job_dir = w1._job_dir(7)
    assert job_dir is not None
    outbox = job_dir / rw.OUTBOX_FILE
    if how == "empty":
        outbox.write_bytes(b"")  # rename 은 됐는데 fsync 전에 OS 가 죽었다
    elif how == "bad_json":
        outbox.write_bytes(b'{"acked": 0, "fini')
    else:
        outbox.unlink()
    sid = w1.session_id
    die(w1)
    return sid, job_dir


@pytest.mark.parametrize("how", ["empty", "bad_json", "missing"])
def test_p6_1_g1_a_dead_queue_whose_outbox_cannot_be_read_says_so_and_keeps_the_spool(
    tmp_path, fast, how
):
    """# P6-1 G1 다시 뜬 워커가 `stream.log` 는 있는데 `outbox.json` 이 없거나 비었거나 깨진 잡
    폴더를 만난다. 지금까지는 로그 한 줄 없이 건너뛰어, 성공한 잡이 restarted-without-job 으로
    닫혀도 워커 로그에 이유가 없었다. 한 줄을 남기고, 스풀은 지우지 않고 세션 폴더 밖의 격리
    폴더로 옮긴다 — 등록이 받은 죽은 세션 폴더를 지워도(`_settle_reported`) 남는다."""
    clock = WClock()
    fake = FakeServer(clock)
    sid, job_dir = _dead_job_with(tmp_path, fake, clock, how)
    w2 = restart(tmp_path, fake, clock)
    assert w2.dead_queues() == []
    line = f"dead session {sid[:8]} job #7: outbox unreadable"
    said = [x for x in w2.logs if x.startswith(line)]
    assert len(said) == 1, w2.logs
    assert "the job will close as restarted-without-job" in said[0]
    w2._settle_reported([sid])  # 등록이 D 를 받았다 — D 의 폴더를 지운다
    assert not job_dir.exists()
    kept = list((w2.config.data_path / rw.SESSIONS_DIR).glob(f"unreadable-{sid}-7*"))
    assert len(kept) == 1, list((w2.config.data_path / rw.SESSIONS_DIR).iterdir())
    assert (kept[0] / rw.STREAM_FILE).read_bytes() == b"one\ntwo\n"
    assert str(kept[0]) in said[0]


def test_p6_1_g1_b_a_job_folder_with_neither_file_is_skipped_quietly(tmp_path, fast):
    """# P6-1 G1 보고를 시작하기 전에 죽은 잡(claim.json · proc.json 만)은 보낼 것이 없다 — 줄도
    격리 폴더도 없다."""
    clock = WClock()
    fake = FakeServer(clock)
    w1 = wworker(tmp_path, fake, clock)
    w1.prepare_sessions()
    w1._write_claim(7, False, 90)
    die(w1)
    w2 = restart(tmp_path, fake, clock)
    assert w2.dead_queues() == []
    assert not any("outbox unreadable" in x for x in w2.logs), w2.logs
    assert not list((w2.config.data_path / rw.SESSIONS_DIR).glob("unreadable-*"))


def test_p6_1_g1_c_the_outbox_is_synced_to_disk_before_and_after_the_rename(tmp_path, monkeypatch):
    """# P6-1 G1 원자적 rename 만으로는 OS 가 죽은 뒤 빈 `outbox.json` 이 남을 수 있다. 임시 파일을
    fsync 한 뒤 이름을 바꾸고, 그 폴더도 fsync 한다."""
    import stat as stat_mod

    seen: list[str] = []
    real_fsync, real_replace = os.fsync, os.replace

    def fsync(fd: int) -> None:
        seen.append("dir" if stat_mod.S_ISDIR(os.fstat(fd).st_mode) else "file")
        real_fsync(fd)

    def replace(src: Any, dst: Any) -> None:
        seen.append("rename")
        real_replace(src, dst)

    monkeypatch.setattr(rw.os, "fsync", fsync)
    monkeypatch.setattr(rw.os, "replace", replace)
    ob = rw.JobOutbox(tmp_path / "jobs" / "7")
    ob.finish = {"outcome": SUCCEEDED, "exit_code": 0, "log_bytes": 0}
    ob.save()
    assert seen == ["file", "rename", "dir"], seen
    loaded = rw.JobOutbox.load(tmp_path / "jobs" / "7")
    assert loaded is not None and loaded.finish is not None


def test_p6_1_g2_a_failed_outbox_save_says_so_once_and_leaves_no_temp_file(tmp_path, monkeypatch):
    """# P6-1 G2 디스크가 가득 차면 저장이 `OSError` 다. 지금까지는 조용히 삼켜 finish 가 메모리에만
    있는 줄 몰랐고, 실패한 임시 파일 `.outbox.json.<hex>` 이 가득 찬 디스크에 쌓였다. 한 번 말하고
    (재시작하면 보고를 잃는다), 임시 파일은 지운다."""
    import errno

    folder = tmp_path / "jobs" / "7"
    logs: list[str] = []
    ob = rw.JobOutbox(folder)
    ob.log = logs.append
    ob.save()  # 처음 저장은 된다

    def full(src: Any, dst: Any) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(rw.os, "replace", full)
    ob.finish = {"outcome": SUCCEEDED, "exit_code": 0, "log_bytes": 0}
    ob.save()
    ob.acked = 3
    ob.save()
    said = [x for x in logs if "cannot save" in x]
    assert len(said) == 1, logs
    assert said[0].startswith("#7 report: cannot save outbox.json")
    assert "a restart before it is sent loses it" in said[0]
    assert sorted(p.name for p in folder.iterdir()) == [rw.OUTBOX_FILE], list(folder.iterdir())


@pytest.mark.parametrize(
    ("code", "args"),
    [("worker_self_stopped", {"seconds": 75}), ("snapshot_unreachable", {}), (None, None)],
    ids=["self_stopped", "snapshot_unreachable", "no_code"],
)
def test_p6_1_g3_a_resent_copy_of_the_workers_own_lost_finish_is_not_a_late_result(srv, code, args):
    """# P6-1 G3 워커가 스스로 `lost` 로 보고한 finish 가 기록되고 2xx 만 잃었다. 같은 finish 를
    다시 보내면 잡은 `lost` · 같은 세션 · 펜싱 아님이라 지금까지 `late_result` 로 남았다 — 그 `lost`
    는 서버가 닫은 것이 아니라 이 finish 가 만든 것이다. 명세(중복 finish, RL21 a)는 409 이고
    기록하지 않는다. `log_complete` 도 다시 쓰지 않는다."""
    jid = running(srv)
    assert post_log(srv, jid, b"one\n", 0)[0] == 200
    body: dict[str, Any] = {"exit_code": None, "log_bytes": 4, "group_gone": True}
    if code is not None:
        body.update(summary_code=code, summary_args=args)
    status, first = fin(srv, jid, LOST, **body)
    assert status == 200, first
    assert job_doc(srv, jid)["log_complete"] is True
    srv.clock.advance(5)
    status, again = fin(srv, jid, LOST, **{**body, "log_bytes": 40})
    assert status == 409 and again["code"] == "job_closed" and again["state"] == LOST, again
    assert again["late_result_recorded"] is False, again
    assert late_events(srv, jid) == []
    doc = job_doc(srv, jid)
    assert doc["late_result"] is None and doc["log_complete"] is True, doc


def test_p6_1_g3_b_a_lost_close_by_the_server_still_takes_the_workers_lost_finish_late(srv):
    """# P6-1 G3 서버가 먼저 닫은 `lost`(유예 만료) 뒤에 온 워커의 자기 멈춤 finish 는 여전히 늦은
    결과다 — 중복 판정은 그 잡을 닫은 것이 이 세션의 finish 였을 때만이다."""
    jid = running(srv)
    close_lost(srv, jid)
    status, body = fin(
        srv,
        jid,
        LOST,
        exit_code=None,
        summary_code="worker_self_stopped",
        summary_args={"seconds": 75},
        group_gone=True,
    )
    assert status == 409 and body["late_result_recorded"] is True, body
    assert len(late_events(srv, jid)) == 1


def test_p6_1_g4_a_a_missing_bundle_file_finishes_with_upload_failed(tmp_path, fast):
    """# P6-1 G4 큐의 번들 파일이 없다(지워졌다). `upload_bundle` 의 `stat()` 은 `_request` 밖이라
    `FileNotFoundError` 가 보내는 스레드를 죽여, 레인과 잡이 재시작까지 잡혀 있었다. 산출물 실패
    갈래(`upload_failed`)로 finish 를 보낸다."""
    clock = WClock()
    fake = FakeServer(clock)
    w, ob, s = live_sender(tmp_path, fake, clock)
    ob.append(b"one\n")
    ob.bundle = {"path": str(tmp_path / "gone.tar"), "state": "pending", "sha256": None}
    end_job(w, ob, artifacts={"state": "ready", "file_count": 1, "total_bytes": 2})
    s.start()
    s.thread.join(10)
    assert ob.done and not s.gave_up, w.logs
    sent = [c for c in fake.calls if c[0] == "finish"]
    assert len(sent) == 1
    arts = sent[0][5]["artifacts"]
    assert (arts["state"], arts["reason_code"]) == ("failed", "upload_failed"), arts
    assert sum(1 for x in w.logs if "cannot read the bundle" in x) == 1, w.logs


class _ScriptedInner:
    """`WorkerClient._inner` 대역 — 길마다 정한 (status, headers, raw) 를 앞에서부터 돌려준다."""

    def __init__(self, script: dict[str, list[bytes]]) -> None:
        self.script = script
        self.calls: list[str] = []

    def _request(self, method: str, path: str, **kw: Any) -> tuple[int, dict[str, str], bytes]:
        route = path.rsplit("/", 1)[-1]
        self.calls.append(route)
        queue = self.script.get(route) or []
        raw = queue.pop(0) if queue else b'{"offset": 4}' if route == "log" else b"{}"
        return 200, {}, raw


def _scripted_client(script: dict[str, list[bytes]]):
    from remote_ci_monitor.client import WorkerClient

    client = WorkerClient("http://fake", "t")
    inner = _ScriptedInner(script)
    client._inner = inner  # type: ignore[assignment]
    return client, inner


@pytest.mark.parametrize("route", ["log", "finish", "artifacts"])
def test_p6_1_g4_b_a_2xx_answer_that_is_not_json_is_a_client_error(tmp_path, route):
    """# P6-1 G4 2xx 인데 본문이 JSON 이 아니다(앞의 프록시 · 깨진 응답). 지금까지는
    `json.loads` 의 예외가 그대로 나가 보내는 스레드를 죽였다. `ClientError` 로 바꿔 다시 해 본다
    — 서버가 답은 했으니 HTTP 오류 응답과 같이 센다(5xx 갈래)."""
    client, _ = _scripted_client({route: [b"<html>proxy</html>"]})
    tar = tmp_path / "b.tar"
    tar.write_bytes(b"x")
    got: BaseException | None = None
    try:
        if route == "log":
            client.log(7, b"one\n", offset=0)
        elif route == "finish":
            client.finish(7, SUCCEEDED, 0, None)
        else:
            client.upload_bundle(7, tar)
    except Exception as e:  # noqa: BLE001 — 무엇이 나오는지 본다
        got = e
    assert isinstance(got, ClientError), repr(got)
    assert got.status >= 500, got.status


def test_p6_1_g4_c_a_sender_retries_a_2xx_answer_that_is_not_json(tmp_path, fast):
    """# P6-1 G4 진짜 클라이언트로 — 깨진 2xx 본문 뒤에도 보내는 스레드가 살아 로그와 finish 를
    보낸다."""
    clock = WClock()
    client, inner = _scripted_client({"log": [b"not json"], "finish": [b"also not json"]})
    w = wworker(tmp_path, client, clock)  # type: ignore[arg-type]
    ob = rw.JobOutbox(None)
    ob.append(b"one\n")
    end_job(w, ob)
    s = rw._JobSender(w, 7, ob).start()
    s.thread.join(10)
    assert ob.done and not s.gave_up, w.logs
    assert inner.calls.count("log") == 2 and inner.calls.count("finish") == 2, inner.calls


def test_p6_1_g4_d_an_unexpected_error_backs_off_and_retries_saying_so_once(tmp_path, fast):
    """# P6-1 G4 그 밖의 예상 못 한 예외(버그 · 로컬 고장)는 스레드를 죽이지 않고 물러서며 다시
    해 본다. 같은 결정적 원인이 매번 스레드를 죽이면 살아 있는 프로세스가 레인과 잡을 재시작까지
    잡고, 죽은 큐 보내기도 같은 오류로 죽는다. 줄은 한 번이다."""
    clock = WClock()
    fake = FakeServer(clock)
    w, ob, s = live_sender(tmp_path, fake, clock)
    ob.append(b"one\n")
    end_job(w, ob)
    fake.script["finish"] = [RuntimeError("boom")] * 5
    s.start()
    s.thread.join(10)
    assert ob.done and not s.gave_up, w.logs
    assert len([c for c in fake.calls if c[0] == "finish"]) == 6
    said = [x for x in w.logs if "unexpected" in x]
    assert len(said) == 1, w.logs
    assert said[0].startswith("#7 finish: unexpected RuntimeError")


def test_p6_1_g4_e_a_finished_job_stuck_on_an_unexpected_error_sends_the_finish_after_300s(
    tmp_path, fast
):
    """# P6-1 G4 끝난 잡의 로그 단계가 예상 못 한 예외로 계속 막히면, 못 읽는 스풀과 같이 끝난 지
    300초 뒤 finish 가 먼저 간다 — 24시간 묶이면 서버에서 잡이 running 이고 레인도 잡혀 있다."""
    clock = WClock()
    fake = FakeServer(clock)
    w, ob, s = live_sender(tmp_path, fake, clock)
    ob.append(b"one\n")
    end_job(w, ob)
    fake.script["log"] = [RuntimeError("boom")] * 100000
    s.start()
    try:
        assert wait_for(lambda: any("unexpected" in x for x in w.logs), 5), w.logs
        time.sleep(0.05)
        assert not ob.done
        clock.advance(rw.FINISH_HOLD_SECONDS + 1)
        s.wake.set()
        assert wait_for(lambda: ob.done, 10), w.logs
    finally:
        s.halt.set()
    assert fake.routes()[-1] == "finish"
    assert fake.calls[-1][5]["log_bytes"] == 4


def test_p6_1_g6_the_worker_reads_late_result_recorded_and_hold_released(srv, tmp_path, fast):
    """# P6-1 G6 lost 로 닫힌 잡에 보낸 finish 의 409 `job_closed` 에는 `late_result_recorded` ·
    `hold_released` 가 온다. 워커가 그 키를 읽지 않아, 기록되지 않은 늦은 결과(경합)가 조용히
    버려졌다. 진짜 서버 → 진짜 워커로 — 두 키를 읽어 줄을 남긴다."""
    from remote_ci_monitor.client import WorkerClient

    jid = running(srv, "ship")
    close_lost(srv, jid, hold="worker_unreachable")
    assert srv.store.has_open_hold(jid)
    clock = WClock()
    client = WorkerClient(f"http://127.0.0.1:{srv.port}", srv.tokens[W])
    w = wworker(tmp_path, client, clock)  # type: ignore[arg-type]
    w.session_id = srv.session(W)
    w._bind_session(w.client)
    w.session_dir = tmp_path / "wd" / rw.SESSIONS_DIR / w.session_id
    ob = ended_queue(w, jid, b"")
    s = rw._JobSender(w, jid, ob).start()
    s.thread.join(10)
    assert ob.done, w.logs
    assert f"#{jid} late result recorded" in w.logs, w.logs
    assert f"#{jid} hold released" in w.logs, w.logs
    assert not srv.store.has_open_hold(jid)
    # 같은 finish 가 또 간다(재시작한 워커의 큐) — 서버는 기록하지 않았다고 답한다
    ob2 = ended_queue(w, jid, b"")
    s2 = rw._JobSender(w, jid, ob2).start()
    s2.thread.join(10)
    assert ob2.done, w.logs
    not_recorded = [x for x in w.logs if x.startswith(f"#{jid} late result not recorded — ")]
    assert len(not_recorded) == 1, w.logs
    assert len(late_events(srv, jid)) == 1


def test_p6_1_g5_runner_output_never_makes_the_worker_cut_a_line(tmp_path, fast):
    """# P6-1 G5 서버는 조각마다 가리고, 두 조각에 걸친 비밀은 이미 커밋된 앞부분을 못 고친다.
    워커가 줄 가운데서 자르는 것은 4 MiB(`LOG_PIECE_MAX_BYTES`)를 넘는 한 줄뿐이다. 러너는 줄 끝
    없는 출력을 64 KiB(`MAX_LINE_BYTES`)마다 끊어 흘리므로 러너가 낸 줄은 많아야
    `MAX_LINE_BYTES + READ_CHUNK` 이다 — 한 조각(256 KiB) 안이다. 진짜 러너의 출력을 진짜 보내는
    스레드에 넣어, 도는 잡의 조각이 모두 줄 끝에서 끝나는지 본다."""
    from remote_ci_monitor import runner
    from test_runner import FileMaterializer, RecordingObserver, make_spec
    from test_runner import run as run_runner
    from test_runner import sh as shell

    longest = runner.MAX_LINE_BYTES + runner.READ_CHUNK
    assert longest + 1 <= rw.LOG_BATCH_BYTES < rw.LOG_PIECE_MAX_BYTES
    obs = RecordingObserver()
    payload = b"s" * (rw.LOG_BATCH_BYTES + 3 * runner.MAX_LINE_BYTES + 5)
    spec = make_spec(tmp_path, shell("cat big.txt; echo; echo done"))
    result = run_runner(spec, obs, materialize=FileMaterializer({"big.txt": payload}, observer=obs))
    assert result.rc == 0
    out = obs.out
    assert out.replace(b"\n", b"") == payload + b"done"
    lines = out.split(b"\n")[:-1]
    assert max(len(x) for x in lines) <= longest, sorted({len(x) for x in lines})
    clock = WClock()
    fake = FakeServer(clock)
    w, ob, s = live_sender(tmp_path, fake, clock)
    ob.append(out)
    s.start()
    try:
        assert wait_for(lambda: ob.acked == len(out), 10), ob.acked
    finally:
        s.halt.set()
    pieces = [c[4] for c in fake.calls if c[0] == "log"]
    assert ob.finish is None and all(p.endswith(b"\n") for p in pieces), [len(p) for p in pieces]
    assert all(len(p) <= rw.LOG_BATCH_BYTES for p in pieces), [len(p) for p in pieces]


# ── batch-5: P6 재확인 LOW ────────────────────────────────────────────────────────────────────


def test_b5_1_ok_at_is_replaced_without_fsync_while_the_report_queue_is_synced(
    tmp_path, monkeypatch
):
    """# batch-5 #1 `ok_at.json` 은 heartbeat 의 도우미 스레드가 2xx 를 받은 뒤 H 마다 쓴다. 무거운
    빌드 I/O 에서 fsync 가 멈추면 그 회차가 시한(`HEARTBEAT_DEADLINE_SECONDS`)을 넘겨 실패로
    세진다. 그 파일은 내구성이 필요한 보고가 아니다 — 원자적 이름 바꾸기만 한다. 보고 큐
    (`outbox.json`)와 `claim.json` · `proc.json` 은 그대로 fsync 한다."""
    clock = WClock()
    fake = FakeServer(clock)
    w = wworker(tmp_path, fake, clock)
    w.prepare_sessions()
    assert w.session_dir is not None
    synced: list[int] = []
    real_fsync = os.fsync

    def fsync(fd: int) -> None:
        synced.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(rw.os, "fsync", fsync)
    w.mark_ok(*w._sent_at())
    ok_at = rw._read_json(w.session_dir / "ok_at.json")
    assert ok_at is not None and isinstance(ok_at.get("wall"), str), ok_at
    assert synced == [], "ok_at.json was synced on the heartbeat path"
    assert not [p for p in w.session_dir.iterdir() if p.name.startswith(".ok_at")]
    ob = rw.JobOutbox(w._job_dir(7))
    ob.save()
    assert len(synced) == 2  # 파일 · 폴더
    w._write_claim(7, False, 90)
    w._write_proc(7, None)
    assert len(synced) == 6


def _run_bounded(fn: Any, w: rw.RemoteWorker, seconds: float = 10.0) -> bool:
    """`fn` 을 스레드에서 부르고 `seconds` 안에 끝나는지 본다. 안 끝나면 워커를 멈춰 거둔다."""
    t = threading.Thread(target=fn, daemon=True)
    t.start()
    t.join(seconds)
    ended = not t.is_alive()
    if not ended:
        w.stopping.set()
        t.join(5)
    return ended


def test_b5_2_a_a_dead_queue_stuck_on_a_local_error_says_local_errors_after_300s(tmp_path, fast):
    """# batch-5 #2 죽은 세션 D 의 큐에서 finish 가 예상 못 한 로컬 오류로 계속 실패한다.
    batch-7 에서 바뀌었다: 로컬 오류도 서버 오류와 같은 300초 상한 안에서 다시 해 본다(한 번
    지나가는 오류일 수 있다). 상한에 닿으면 등록하고, 그 줄은 서버 탓만 하지 않는다 —
    「server errors or local errors」."""
    clock = WClock()
    fake = FakeServer(clock, tick=40)
    w1 = wworker(tmp_path, fake, clock)
    w1.prepare_sessions()
    ended_queue(w1, 7, b"x\n")
    sid = w1.session_id
    die(w1)
    fake.script["finish"] = [RuntimeError("boom")] * 100000
    w2 = restart(tmp_path, fake, clock)
    assert _run_bounded(w2.send_dead_queues, w2), "registration waits forever on a local error"
    said = [x for x in w2.logs if "queue not delivered" in x or "local error" in x]
    assert said == [
        f"dead session {sid[:8]} queue not delivered after 300s of server errors or local errors"
        " — registering; job #7 will close as restarted-without-job"
    ], w2.logs
    assert clock.wall - T0W >= timedelta(seconds=300)
    assert not [c for c in fake.calls if c[0] == "register"]


def test_b5_2_b_a_dead_queue_whose_log_fails_locally_still_sends_the_finish_first(tmp_path, fast):
    """# batch-5 #2 로그 단계만 로컬 오류로 막힌 끝난 잡은 등록이 기다린다 — 끝난 지 300초 뒤
    finish 가 먼저 간다(`_finish_first`). 그 시간은 300초 등록 대기에 세지 않는다."""
    clock = WClock()
    fake = FakeServer(clock, tick=40)
    w1 = wworker(tmp_path, fake, clock)
    w1.prepare_sessions()
    ended_queue(w1, 7, b"x\n")
    die(w1)
    fake.script["log"] = [RuntimeError("boom")] * 100000
    w2 = restart(tmp_path, fake, clock)
    assert _run_bounded(w2.send_dead_queues, w2)
    assert [c[3] for c in fake.calls if c[0] == "finish"] == [SUCCEEDED], w2.logs
    assert not any("queue not delivered" in x or "local error" in x for x in w2.logs), w2.logs


@pytest.mark.parametrize(
    "refusal",
    [http(400), http(409, "job_closed", state=LOST)],
    ids=["dropped_4xx", "job_closed"],
)
def test_b5_3_a_dropped_item_clears_the_local_error_mark(tmp_path, fast, refusal):
    """# batch-5 #3 로그 단계가 예상 못 한 예외로 실패해 `local_error` 가 섰다. 다음 시도가 4xx 로
    버려지거나 409 `job_closed` 로 지워지면 그 표식은 낡았다. 지우지 않으면 끝난 지 300초가
    지난 잡에서 산출물을 올려 보지도 않고 finish 를 먼저 보낸다(산출물 `upload_failed`)."""
    clock = WClock()
    fake = FakeServer(clock)
    w, ob, s = live_sender(tmp_path, fake, clock)
    tar = tmp_path / "bundle.tar"
    tar.write_bytes(bundle_bytes())
    ob.append(b"one\n")
    ob.bundle = {"path": str(tar), "state": "pending", "sha256": None}
    end_job(w, ob, artifacts={"state": "ready", "file_count": 1, "total_bytes": 2})
    fake.script["log"] = [RuntimeError("boom"), refusal]
    with pytest.raises(RuntimeError):
        s.attempt("log")
    assert s._unexpected("log", RuntimeError("boom")) is False  # 보내는 스레드가 하는 일
    assert s.attempt("log") is True and ob.log_stopped
    clock.advance(rw.FINISH_HOLD_SECONDS + 1)
    assert s.next_step() == "artifacts"


def test_b5_5_a_a_failed_save_of_a_dead_queue_names_the_dead_session(tmp_path, fast, monkeypatch):
    """# batch-5 #5 죽은 세션 D 의 큐에서 저장이 실패하면 그 줄도 다른 D 의 줄처럼 `dead session`
    으로 시작한다 — `#7` 만 보고는 이 프로세스의 잡 #7 과 구별할 수 없다."""
    import errno

    clock = WClock()
    fake = FakeServer(clock)
    w1 = wworker(tmp_path, fake, clock)
    w1.prepare_sessions()
    ended_queue(w1, 7, b"x\n")
    sid = w1.session_id
    die(w1)
    w2 = restart(tmp_path, fake, clock)
    (sender,) = w2.dead_queues()

    def full(src: Any, dst: Any) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(rw.os, "replace", full)
    sender.ob.save()
    said = [x for x in w2.logs if "cannot save" in x]
    assert len(said) == 1, w2.logs
    assert said[0].startswith(f"dead session {sid[:8]} #7 report: cannot save outbox.json"), said


def test_b5_5_b_a_folder_that_cannot_be_synced_is_said_once(tmp_path, fast, monkeypatch):
    """# batch-5 #5 폴더 fsync 가 실패하면(일부 파일 시스템) 지금까지는 조용히 삼켰다. 이름 바꾸기는
    끝났으니 저장은 실패가 아니지만, OS 가 죽으면 마지막 저장을 잃을 수 있다 — 프로세스마다 한
    번 말한다."""
    import errno
    import stat as stat_mod

    clock = WClock()
    fake = FakeServer(clock)
    w1 = wworker(tmp_path, fake, clock)
    w1.prepare_sessions()
    ended_queue(w1, 7, b"x\n")
    die(w1)
    real_fsync = os.fsync

    def fsync(fd: int) -> None:
        if stat_mod.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(errno.EINVAL, "Invalid argument")
        real_fsync(fd)

    monkeypatch.setattr(rw.os, "fsync", fsync)
    w2 = restart(tmp_path, fake, clock)  # owner.json
    (sender,) = w2.dead_queues()
    sender.ob.save()
    sender.ob.save()
    w2._write_claim(8, False, 90)
    said = [x for x in w2.logs if "cannot sync" in x]
    assert said == [
        "session folder: cannot sync a folder to disk (Invalid argument) — an OS crash may lose"
        " the last saved report"
    ], w2.logs
    assert not any("cannot save" in x for x in w2.logs), w2.logs
    assert rw._read_json(w2._job_dir(8) / "claim.json") is not None


def test_b5_6_a_a_finish_stuck_on_an_unexpected_error_is_dropped_after_24h(tmp_path, fast):
    """# batch-5 #6 finish 단계 자체가 예상 못 한 예외로 계속 실패해도 재전송 창(24시간)이 끝나면
    큐 항목을 버리고 한 줄을 남긴다 — 보내는 스레드가 끝나야 레인이 풀린다(§4.5 「재전송 창」)."""
    clock = WClock()
    fake = FakeServer(clock, tick=3 * 3600)
    w, ob, s = live_sender(tmp_path, fake, clock)
    ob.append(b"a\n")
    end_job(w, ob)
    fake.script["finish"] = [RuntimeError("boom")] * 1000
    s.start()
    try:
        s.thread.join(5)
        assert not s.thread.is_alive(), "the sender retries the finish past 24h"
    finally:
        s.halt.set()
    assert ob.done
    assert "#7 report: finish not delivered in 24h — dropping the report" in w.logs, w.logs
    assert sum(1 for x in w.logs if "unexpected RuntimeError" in x) == 1, w.logs


def test_b5_6_b_a_readable_outbox_of_a_dead_session_is_not_set_aside(tmp_path, fast):
    """# batch-5 #6 못 읽는 outbox 만 격리 폴더로 간다 — 읽히는 outbox 는 그 자리에서 보내고, 줄도
    격리 폴더도 없다."""
    clock = WClock()
    fake = FakeServer(clock)
    w1 = wworker(tmp_path, fake, clock)
    w1.prepare_sessions()
    ended_queue(w1, 7, b"one\ntwo\n")
    job_dir = w1._job_dir(7)
    assert job_dir is not None
    die(w1)
    w2 = restart(tmp_path, fake, clock)
    assert [s.job_id for s in w2.dead_queues()] == [7]
    assert not any("outbox unreadable" in x for x in w2.logs), w2.logs
    assert not list((w2.config.data_path / rw.SESSIONS_DIR).glob("unreadable-*"))
    assert (job_dir / rw.STREAM_FILE).read_bytes() == b"one\ntwo\n"
    assert rw.JobOutbox.load(job_dir) is not None


# ── batch-6: 끝난 잡의 밀린 phase 가 finish 를 막지 않는다 ────────────────────────


def _phase_stuck(tmp_path: Path, tick: float, failure: Any):
    """끝난 잡의 큐에 phase 만 남았고 그 phase 가 늘 `failure` 로 실패한다."""
    clock = WClock()
    fake = FakeServer(clock, tick=tick)
    w, ob, s = live_sender(tmp_path, fake, clock)
    ob.phase = "running"
    end_job(w, ob)
    fake.script["phase"] = [failure] * 100000
    return clock, fake, w, ob, s


def test_b6_1_a_a_finished_job_whose_phase_keeps_getting_5xx_sends_the_finish_after_300s(
    tmp_path, fast
):
    """# batch-6 #1 phase 는 결과가 아니라 진행 표시다. 끝난 잡의 phase 가 5xx 로 계속 막히면
    로그 · 산출물의 5xx 와 같이 끝난 지 300초 뒤 finish 가 먼저 간다(결정 324). 그러지 않으면
    살아 있는 워커가 phase 를 끝없이 다시 보내고, 서버의 잡은 running 으로 남아 레인이 잡힌다."""
    clock, fake, w, ob, s = _phase_stuck(tmp_path, 60, http(503))
    s.start()
    try:
        s.thread.join(5)
        assert ob.done, "the finish never goes behind a phase that keeps failing"
    finally:
        s.halt.set()
    fins = [c for c in fake.calls if c[0] == "finish"]
    assert [c[3] for c in fins] == [SUCCEEDED]
    assert timedelta(seconds=300) <= clock.wall - T0W < timedelta(hours=1)
    assert fake.calls[fake.calls.index(fins[0]) + 1 :] == []  # 남은 phase 는 버린다
    said = [x for x in w.logs if "phase" in x and "finish" in x]
    assert said == [
        "#7 report: phase still not delivered 300s after the job ended — sending the finish"
        " first, the phase is dropped"
    ], w.logs


def test_b6_1_b_a_finished_job_whose_phase_fails_unexpectedly_sends_the_finish_after_300s(
    tmp_path, fast
):
    """# batch-6 #1 phase 단계가 예상 못 한 예외로 계속 실패해도 같다 — 끝난 지 300초 뒤 finish
    가 먼저 간다(P6-1 G4 의 로그 단계와 같은 규칙)."""
    clock, fake, w, ob, s = _phase_stuck(tmp_path, 0, RuntimeError("boom"))
    s.start()
    try:
        assert wait_for(lambda: any("unexpected" in x for x in w.logs), 5), w.logs
        time.sleep(0.05)
        assert not ob.done  # 300초 전에는 순서를 지킨다
        clock.advance(rw.FINISH_HOLD_SECONDS + 1)
        s.wake.set()
        assert wait_for(lambda: ob.done, 5), w.logs
    finally:
        s.halt.set()
    assert [c[3] for c in fake.calls if c[0] == "finish"] == [SUCCEEDED]
    assert fake.routes()[-1] == "finish"
    assert sum(1 for x in w.logs if "sending the finish first" in x) == 1, w.logs


def test_b6_1_c_a_finished_job_whose_phase_cannot_reach_the_server_sends_the_finish_after_24h(
    tmp_path, fast
):
    """# batch-6 #1 연결 실패면 finish 도 갈 수 없으니 순서를 지킨다(결정 324). 그래도 재전송 창
    (24시간, 결정 264)이 끝나면 로그처럼 phase 도 포기하고 finish 를 보낸다."""
    clock, fake, w, ob, s = _phase_stuck(tmp_path, 3 * 3600, conn_fail())
    s.start()
    try:
        s.thread.join(5)
        assert ob.done, "the finish never goes behind a phase that cannot be sent"
    finally:
        s.halt.set()
    assert [c[3] for c in fake.calls if c[0] == "finish"] == [SUCCEEDED]
    assert clock.wall - T0W >= timedelta(hours=24)
    assert "#7 report: phase not delivered in 24h — sending the finish without it" in w.logs


def test_b6_2_a_running_job_keeps_retrying_its_phase_and_then_sends_the_log(tmp_path, fast):
    """# batch-6 #3 잡이 도는 동안에는 phase 가 몇 번 · 몇 시간 실패해도 포기하지 않는다(§4.5
    「잡이 도는 동안에는 횟수와 시간에 상한이 없다」). 그동안 로그는 phase 뒤에 줄 서고, phase 가
    가면 이어 간다. 끝나지 않은 잡에는 finish 가 없다."""
    clock = WClock()
    fake = FakeServer(clock, tick=600)
    w, ob, s = live_sender(tmp_path, fake, clock)
    ob.phase = "running"
    ob.append(b"one\n")
    fake.script["phase"] = [http(503)] * 100 + [RuntimeError("boom")] * 100  # 33시간
    s.start()
    try:
        assert wait_for(lambda: bytes(fake.received.get(7, b"")) == b"one\n", 10), w.logs
        assert s.alive() and not ob.done
    finally:
        s.halt.set()
    routes = fake.routes()
    assert routes.count("phase") == 201 and "finish" not in routes
    assert routes.index("log") > max(i for i, r in enumerate(routes) if r == "phase")
    assert ob.phase is None


def test_b6_3_a_dead_queue_whose_phase_fails_locally_still_sends_the_finish_first(tmp_path, fast):
    """# batch-6 #1 죽은 세션 D 의 끝난 잡에서 phase 만 로컬 오류로 막혀도 등록은 기다린다 — 끝난
    지 300초 뒤 finish 가 먼저 간다. 기다리지 않으면 그 잡은 restarted-without-job 으로 닫혀 끝난
    잡의 결과를 잃는다(batch-5 #2 의 로그 단계와 같다)."""
    clock = WClock()
    fake = FakeServer(clock)  # 시계는 시험이 민다 — 등록이 그 큐를 기다리는지 먼저 본다
    w1 = wworker(tmp_path, fake, clock)
    w1.prepare_sessions()
    ob = ended_queue(w1, 7, b"")  # 로그는 다 갔다 — 남은 것은 phase 와 finish 뿐
    ob.phase = "running"
    ob.save()
    die(w1)
    fake.script["phase"] = [RuntimeError("boom")] * 1000000
    w2 = restart(tmp_path, fake, clock)
    t = threading.Thread(target=w2.send_dead_queues, daemon=True)
    t.start()
    try:
        assert wait_for(lambda: any("unexpected" in x for x in w2.logs), 5), w2.logs
        time.sleep(0.3)  # 등록 쪽은 0.05초마다 본다 — 기다리지 않는 큐는 이 사이에 멈춘다
        assert not any("local error" in x for x in w2.logs), w2.logs
        clock.advance(rw.FINISH_HOLD_SECONDS + 1)
        t.join(10)
        assert not t.is_alive(), "registration still waits after the finish could go first"
    finally:
        w2.stopping.set()
        t.join(5)
    assert [c[3] for c in fake.calls if c[0] == "finish"] == [SUCCEEDED], w2.logs
    assert not any("queue not delivered" in x or "local error" in x for x in w2.logs), w2.logs


# ── batch-7: 죽은 세션 큐의 로컬 오류도 300초 상한 안에서 다시 해 본다 ──────────────────


def _dead_acked_queue(tmp_path: Path, fake: FakeServer, clock: WClock) -> str:
    """로그를 다 보낸(`acked == written > 0`) 끝난 잡의 큐를 남기고 죽은 프로세스. 다음 프로세스는
    로그를 보내기 전에 빈 본문 질의(`query`)부터 한다(V2-13)."""
    w1 = wworker(tmp_path, fake, clock)
    w1.prepare_sessions()
    ended_queue(w1, 7, b"one\n", acked=4)
    fake.received[7] = bytearray(b"one\n")
    sid = w1.session_id
    die(w1)
    return sid


def test_b7_1_a_a_dead_queue_whose_query_and_finish_fail_locally_registers_after_300s(
    tmp_path, fast
):
    """# batch-7 #1 로그를 다 보낸 끝난 잡의 D 큐에서 오프셋 질의와 finish 가 예상 못 한 로컬
    오류로 계속 실패한다. 등록은 300초 상한에서 멈추고 줄은 로컬 오류도 말한다. 전에는 질의를 막힌
    단계로 보지 않아 finish 가 먼저 가지도, 300초가 흐르지도 않아 등록이 영원히 기다렸다."""
    clock = WClock()
    fake = FakeServer(clock, tick=40)
    sid = _dead_acked_queue(tmp_path, fake, clock)
    fake.script["log"] = [RuntimeError("boom")] * 100000
    fake.script["finish"] = [RuntimeError("boom")] * 100000
    w2 = restart(tmp_path, fake, clock)
    assert _run_bounded(w2.send_dead_queues, w2), "registration waits forever on a stuck query"
    assert [c[3:5] for c in fake.calls if c[0] == "log"][0] == (4, b"")  # 질의였다
    assert "finish" in fake.routes()  # 끝난 지 300초 뒤 finish 가 먼저 가 봤다
    said = [x for x in w2.logs if "queue not delivered" in x]
    assert said == [
        f"dead session {sid[:8]} queue not delivered after 300s of server errors or local errors"
        " — registering; job #7 will close as restarted-without-job"
    ], w2.logs
    assert not [c for c in fake.calls if c[0] == "register"]


def test_b7_1_b_a_finished_job_whose_offset_query_fails_unexpectedly_sends_the_finish_after_300s(
    tmp_path, fast
):
    """# batch-7 #1 다시 뜬 프로세스의 끝난 잡이 로그를 다 보냈고(`acked == written`) 오프셋 질의만
    예상 못 한 예외로 막혔다. 질의도 막힌 단계다 — 끝난 지 300초 뒤 finish 가 먼저 간다."""
    clock = WClock()
    fake = FakeServer(clock)
    w = wworker(tmp_path, fake, clock)
    ob = rw.JobOutbox(None)
    ob.append(b"one\n")
    ob.acked = 4
    end_job(w, ob)
    s = rw._JobSender(w, 7, ob, resumed=True)
    fake.script["log"] = [RuntimeError("boom")] * 100000
    s.start()
    try:
        assert wait_for(lambda: any("unexpected" in x for x in w.logs), 5), w.logs
        time.sleep(0.05)
        assert not ob.done  # 300초 전에는 순서를 지킨다
        clock.advance(rw.FINISH_HOLD_SECONDS + 1)
        assert wait_for(lambda: ob.done, 5), w.logs
    finally:
        s.halt.set()
    fins = [c for c in fake.calls if c[0] == "finish"]
    assert [(c[3], c[5]["log_bytes"]) for c in fins] == [(SUCCEEDED, 4)], fake.calls


def test_b7_2_a_a_dead_queue_finish_that_fails_locally_once_still_closes_the_job(
    tmp_path, fast, monkeypatch
):
    """# batch-7 #2 D 큐의 finish 가 로컬 오류로 한 번 실패하고 다음 시도에 간다. 로컬 오류는 한 번
    지나가는 것일 수 있다 — 등록은 그 큐를 멈추지 않고 기다려 잡이 진짜 결과로 닫힌다. 전에는 첫
    실패 뒤 0.05초 안에 멈춰 잡이 restarted-without-job 으로 닫혔다."""
    monkeypatch.setattr(rw, "BACKOFF_START_SECONDS", 0.3)  # 등록 쪽이 여러 번 본다
    monkeypatch.setattr(rw, "BACKOFF_MAX_SECONDS", 0.3)
    clock = WClock()
    fake = FakeServer(clock)
    w1 = wworker(tmp_path, fake, clock)
    w1.prepare_sessions()
    ended_queue(w1, 7, b"x\n")
    die(w1)
    fake.script["finish"] = [RuntimeError("boom")]
    w2 = restart(tmp_path, fake, clock)
    assert _run_bounded(w2.send_dead_queues, w2)
    assert [c[3] for c in fake.calls if c[0] == "finish"] == [SUCCEEDED, SUCCEEDED], w2.logs
    assert not fake.script["finish"]  # 두 번째 finish 는 성공했다
    assert not any("queue not delivered" in x or "local error" in x for x in w2.logs), w2.logs
    assert not [c for c in fake.calls if c[0] == "register"]


def test_b7_2_b_a_connection_failure_after_a_local_error_still_does_not_count(
    tmp_path, fast, monkeypatch
):
    """# batch-7 #2 결정 337 의 요점은 연결 실패 시간을 세지 않는 것이다. 로컬 오류 뒤에 연결
    실패가 이어지면 그 시간은 세지 않는다 — 로컬 오류의 표식이 연결 실패 시간까지 세게 하면
    등록이 서버가 돌아오기 전에 그 큐를 버린다."""
    monkeypatch.setattr(rw, "BACKOFF_START_SECONDS", 0.3)
    monkeypatch.setattr(rw, "BACKOFF_MAX_SECONDS", 0.3)
    clock = WClock()
    fake = FakeServer(clock)
    w1 = wworker(tmp_path, fake, clock)
    w1.prepare_sessions()
    ended_queue(w1, 7, b"x\n")
    die(w1)
    refused = [threading.Event() for _ in range(3)]
    fake.script["finish"] = [RuntimeError("boom"), conn_fail(), conn_fail(), conn_fail()]
    real_finish = fake.finish

    def finish(job_id, outcome, rc, summary=None, **extra) -> dict[str, Any]:
        n = sum(1 for c in fake.calls if c[0] == "finish")  # 이 시도 앞의 수
        try:
            return real_finish(job_id, outcome, rc, summary, **extra)
        finally:
            if 1 <= n <= 3:
                refused[n - 1].set()

    fake.finish = finish  # type: ignore[method-assign]
    w2 = restart(tmp_path, fake, clock)
    t = threading.Thread(target=w2.send_dead_queues, daemon=True)
    t.start()
    try:
        for ev in refused:
            assert ev.wait(5), w2.logs
            time.sleep(0.1)  # 보내는 쪽은 연결 실패 뒤 물러서 있다
            clock.advance(400)  # 서버가 없는 400초
        t.join(10)
        assert not t.is_alive(), w2.logs
    finally:
        w2.stopping.set()
        t.join(5)
    assert [c[3] for c in fake.calls if c[0] == "finish"] == [SUCCEEDED] * 5, w2.logs
    assert not any("queue not delivered" in x or "local error" in x for x in w2.logs), w2.logs


def test_b7_2_c_the_300s_cap_does_not_cut_off_a_finish_that_has_not_been_tried(
    tmp_path, fast, monkeypatch
):
    """# batch-7 #2 로그 단계가 로컬 오류로 막힌 끝난 잡. 잡이 끝난 직후 다시 뜬 프로세스라 끝난 지
    300초와 등록 대기 300초가 같이 온다. 등록의 상한이 물러서 있는 보내는 스레드를 먼저 멈추면 한
    번도 가 보지 않은 finish 를 잃는다 — 끝난 잡의 finish 는 한 번은 가 본다."""
    monkeypatch.setattr(rw, "BACKOFF_START_SECONDS", 0.5)
    monkeypatch.setattr(rw, "BACKOFF_MAX_SECONDS", 0.5)
    clock = WClock()
    fake = FakeServer(clock)
    w1 = wworker(tmp_path, fake, clock)
    w1.prepare_sessions()
    ended_queue(w1, 7, b"x\n")
    die(w1)
    fake.script["log"] = [RuntimeError("boom")] * 100000
    w2 = restart(tmp_path, fake, clock)
    t = threading.Thread(target=w2.send_dead_queues, daemon=True)
    t.start()
    try:
        assert wait_for(lambda: any("unexpected" in x for x in w2.logs), 5), w2.logs
        time.sleep(0.1)  # 보내는 쪽은 0.5초 물러서 있다 — 등록 쪽은 그동안 0.05초마다 본다
        clock.advance(rw.FINISH_HOLD_SECONDS + 1)
        t.join(10)
        assert not t.is_alive(), w2.logs
    finally:
        w2.stopping.set()
        t.join(5)
    assert [c[3] for c in fake.calls if c[0] == "finish"] == [SUCCEEDED], w2.logs
    assert not any("queue not delivered" in x for x in w2.logs), w2.logs


def test_b7_3_a_finish_sent_first_says_what_it_leaves_behind(tmp_path, fast):
    """# batch-7 #3 phase 와 로그가 함께 밀렸고 phase 가 5xx 다. 끝난 지 300초 뒤 finish 가 먼저
    가고, 한 번도 보내 보지 않은 로그 꼬리는 가지 않는다. 그 사실을 한 줄로 남긴다 — 줄 없이
    버리면 운영자는 `log_complete` 가 왜 거짓인지 모른다."""
    clock = WClock()
    fake = FakeServer(clock, tick=60)
    w, ob, s = live_sender(tmp_path, fake, clock)
    ob.phase = "running"
    ob.append(b"one\ntwo\n")
    end_job(w, ob)
    fake.script["phase"] = [http(503)] * 100000
    s.start()
    try:
        s.thread.join(5)
        assert ob.done
    finally:
        s.halt.set()
    assert "log" not in fake.routes()
    assert [c[5]["log_bytes"] for c in fake.calls if c[0] == "finish"] == [8]
    said = [x for x in w.logs if "not sent" in x]
    assert said == [
        "#7 report: the finish goes first 300s after the job ended; not sent: the phase, the last"
        " 8 bytes of the log (log_complete is false)"
    ], w.logs


# ── batch-8: 빚진 finish 대기의 상한 · 「not sent」 줄의 갈래 ─────────────────────────


class _ClocksStop(FakeServer):
    """처음 `ticks` 번의 요청만 두 시계를 민다 — 그 뒤 두 시계가 다 선다. D 의 클라이언트는
    사본이라 남은 횟수를 사본과 나눈다."""

    def __init__(self, clock: WClock, *, tick: float, ticks: int) -> None:
        super().__init__(clock, tick=tick)
        self.left = [ticks]

    def __copy__(self) -> _ClocksStop:
        other = _ClocksStop.__new__(_ClocksStop)
        other.__dict__.update(self.__dict__)
        return other

    def _next(self, route: str) -> None:
        with self.lock:
            self.left[0] -= 1
            if self.left[0] < 0:
                self.tick = 0.0
        super()._next(route)


def test_b8_1_a_registration_does_not_wait_on_a_finish_whose_end_is_ahead_of_the_clock(
    tmp_path, fast
):
    """# batch-8 #1 벽시계가 뒤로 가 D 큐의 `ended_at` 이 미래다. 그 잡의 나이는 음수라 finish 가
    먼저 가지 않는다. 등록 상한은 「빚진 finish」를 기다리는데, 음수 나이를 빚으로 보면 시계가
    따라잡을 때까지(여기서는 1년) 등록이 기다린다. 음수 나이는 빚진 finish 가 없다.
    batch-9 #3: 등록 대기는 단조 시계로 잰다. 두 시계가 요청 8번(320초) 뒤에 멈추니, 빚으로 보면
    단조 60초 상한도 오지 않아 등록이 끝나지 않는다. 대기 고리의 박자와 상관없이 갈린다."""
    clock = WClock()
    fake = _ClocksStop(clock, tick=40, ticks=8)
    w1 = wworker(tmp_path, fake, clock)
    w1.prepare_sessions()
    ended_queue(w1, 7, b"x\n")
    sid = w1.session_id
    die(w1)
    clock.wall -= timedelta(days=365)  # 벽시계가 뒤로 갔다
    fake.script["log"] = [http(503)] * 1000000
    w2 = restart(tmp_path, fake, clock)
    assert _run_bounded(w2.send_dead_queues, w2, 5), "registration waits for the clock"
    assert "finish" not in fake.routes()
    said = [x for x in w2.logs if "queue not delivered" in x]
    assert said == [
        f"dead session {sid[:8]} queue not delivered after 300s of server errors"
        " — registering; job #7 will close as restarted-without-job"
    ], w2.logs


class _WallStands(FakeServer):
    """요청마다 단조 시계만 `tick` 초 민다 — 벽시계는 서 있다. D 큐 등록 대기는 단조 시계로
    잰다(§4.1.3 「D 큐 등록 대기」). D 의 클라이언트는 사본이라 사본도 이 갈래여야 한다."""

    def __copy__(self) -> _WallStands:
        other = _WallStands.__new__(_WallStands)
        other.__dict__.update(self.__dict__)
        return other

    def _next(self, route: str) -> None:
        with self.lock:
            self.clock.mono += self.tick
        tick, self.tick = self.tick, 0.0
        try:
            super()._next(route)
        finally:
            self.tick = tick


def _stamped(w: rw.RemoteWorker, clock: WClock) -> list[tuple[str, float]]:
    """워커의 로그 줄마다 그때의 단조 시계를 함께 남긴다."""
    stamps: list[tuple[str, float]] = []
    say = w.log

    def log(msg: str) -> None:
        stamps.append((msg, clock.mono))
        say(msg)

    w.log = log  # type: ignore[method-assign]
    return stamps


def test_b8_1_b_the_wait_for_an_owed_finish_has_a_fixed_cap(tmp_path, fast):
    """# batch-8 #1 등록 상한(300초)에 닿았을 때 끝난 잡의 나이가 0 과 300초 사이이고 벽시계가 더
    가지 않는다(시계가 그만큼 뒤로 갔다). finish 는 끝난 지 300초가 될 때까지 먼저 가지 않으니,
    빚진 finish 를 기다리는 등록은 고정된 상한(`OWED_FINISH_WAIT_SECONDS`, 단조 시계) 뒤에 끝나야
    한다. 전에는 그 상한이 없어 시계가 따라잡을 때까지 기다렸다.
    batch-9 #2 · #3: 벽시계를 요청 10번 뒤에 세우던 판은 보내는 스레드가 대기 고리의 0.05초
    박자보다 빨라야 초록이었다. 이제 벽시계는 처음부터 서 있고 단조 시계만 간다 — 박자와
    상관없이 나이가 늘 50초이고, 등록은 단조로 300 + 60초가 지나야 한다."""
    clock = WClock()
    fake = _WallStands(clock, tick=40)
    w1 = wworker(tmp_path, fake, clock)
    w1.prepare_sessions()
    ended_queue(w1, 7, b"x\n")
    sid = w1.session_id
    die(w1)
    clock.wall += timedelta(seconds=50)  # 다시 뜬 때 잡의 나이는 50초이고 그 뒤 벽시계는 선다
    fake.script["log"] = [http(503)] * 1000000
    w2 = restart(tmp_path, fake, clock)
    stamps = _stamped(w2, clock)
    began = clock.mono
    assert _run_bounded(w2.send_dead_queues, w2, 5), "registration waits on an owed finish"
    assert "finish" not in fake.routes()
    age = (clock.wall - T0W).total_seconds()
    assert 0 < age < rw.FINISH_HOLD_SECONDS  # 끝난 지 300초가 안 됐다 — finish 는 아직 빚이다
    said = [x for x in w2.logs if "queue not delivered" in x]
    assert said == [
        f"dead session {sid[:8]} queue not delivered after 300s of server errors"
        " — registering; job #7 will close as restarted-without-job"
    ], w2.logs
    at = [m for x, m in stamps if "queue not delivered" in x][0] - began
    # 빚진 finish 를 정말 기다렸다 — 300초에서 바로 끊지 않았다
    assert at >= rw.DEAD_SESSION_WAIT_SECONDS + rw.OWED_FINISH_WAIT_SECONDS, at


def test_b8_1_c_the_owed_finish_wait_is_one_backoff_and_two_requests():
    """# batch-8 #1 그 상한은 상한에 닿을 때 날아가던 요청 하나 · 백오프 한 번 · finish 요청 하나다
    — 60초. 요청 시한은 워커 클라이언트의 기본값과 같은 상수다."""
    from remote_ci_monitor import client as rc

    req = getattr(rc, "WORKER_REQUEST_TIMEOUT_SECONDS", None)
    assert req == rc.WorkerClient("http://fake", "t").timeout == 15
    assert getattr(rw, "OWED_FINISH_WAIT_SECONDS", None) == rw.BACKOFF_MAX_SECONDS + 2 * req == 60


class _WallSteps(FakeServer):
    """요청마다 두 시계를 `tick` 초 밀고, 첫 요청에서만 벽시계를 `step` 초 더 민다(NTP 가 시계를
    앞으로 맞췄다)."""

    def __init__(self, clock: WClock, *, tick: float, step: float) -> None:
        super().__init__(clock, tick=tick)
        self.step = [step]

    def __copy__(self) -> _WallSteps:
        other = _WallSteps.__new__(_WallSteps)
        other.__dict__.update(self.__dict__)
        return other

    def _next(self, route: str) -> None:
        with self.lock:
            step, self.step[0] = self.step[0], 0.0
            self.clock.wall += timedelta(seconds=step)
        super()._next(route)


def test_b9_3_a_a_wall_clock_step_forward_does_not_shorten_the_dead_session_wait(
    tmp_path, fast, monkeypatch
):
    """# batch-9 #3 D 큐 등록 대기는 단조 시계로 잰다(§4.1.3 「D 큐 등록 대기(3판)」). 기다리는
    중 벽시계가 250초 앞으로 뛰어도 등록은 단조로 300초가 지나야 한다 — 벽시계로 세면 50초 남짓에
    등록해 D 의 큐를 너무 일찍 버린다. finish 는 벽시계로 300초가 되어 먼저 가 보고(500) 빚은
    없다."""
    # 보내는 쪽 백오프를 늘려 대기 고리(0.05초 박자)가 요청 하나하나를 본다 — 아니면 한 박자에
    # 단조 시간이 수백 초씩 흘러 벽시계로 세는 판도 300초를 넘겨 초록일 수 있다
    monkeypatch.setattr(rw, "BACKOFF_START_SECONDS", 0.1)
    monkeypatch.setattr(rw, "BACKOFF_MAX_SECONDS", 0.1)
    clock = WClock()
    fake = _WallSteps(clock, tick=20, step=250)
    w1 = wworker(tmp_path, fake, clock)
    w1.prepare_sessions()
    ended_queue(w1, 7, b"x\n")
    die(w1)
    fake.script["log"] = [http(503)] * 100000
    fake.script["finish"] = [http(500)] * 100000
    w2 = restart(tmp_path, fake, clock)
    stamps = _stamped(w2, clock)
    began = clock.mono
    assert _run_bounded(w2.send_dead_queues, w2, 15), w2.logs
    at = [m for x, m in stamps if "queue not delivered" in x]
    assert at, w2.logs
    assert at[0] - began >= rw.DEAD_SESSION_WAIT_SECONDS, (at[0] - began, w2.logs)
    assert "finish" in fake.routes()  # 먼저 가 본 finish 가 있었다 — 빚을 기다린 것이 아니다


def test_b9_3_b_a_wall_clock_that_went_back_and_stands_does_not_stall_the_dead_session_wait(
    tmp_path, fast
):
    """# batch-9 #3 벽시계가 하루 뒤로 가고 그 뒤 서 있다. 벽시계로 세면 흐른 시간이 늘 0 이라
    등록이 끝없이 기다린다. 단조 시계로 300초가 지나면 등록한다. 끝난 시각이 미래라 빚진 finish
    는 없다."""
    clock = WClock()
    fake = _WallStands(clock, tick=40)
    w1 = wworker(tmp_path, fake, clock)
    w1.prepare_sessions()
    ended_queue(w1, 7, b"x\n")
    sid = w1.session_id
    die(w1)
    clock.wall -= timedelta(days=1)
    fake.script["log"] = [http(503)] * 1000000
    w2 = restart(tmp_path, fake, clock)
    stamps = _stamped(w2, clock)
    began = clock.mono
    assert _run_bounded(w2.send_dead_queues, w2, 5), "the wall clock stalls the dead-session wait"
    said = [x for x in w2.logs if "queue not delivered" in x]
    assert said == [
        f"dead session {sid[:8]} queue not delivered after 300s of server errors"
        " — registering; job #7 will close as restarted-without-job"
    ], w2.logs
    at = [m for x, m in stamps if "queue not delivered" in x][0] - began
    assert at >= rw.DEAD_SESSION_WAIT_SECONDS, at


def test_b8_3_a_finish_sent_first_behind_the_log_names_only_the_log(tmp_path, fast):
    """# batch-8 #3 phase · 묶음 없이 로그만 5xx 로 밀렸다. finish 가 먼저 가며 남긴 로그 꼬리만
    말한다."""
    clock = WClock()
    fake = FakeServer(clock, tick=60)
    w, ob, s = live_sender(tmp_path, fake, clock)
    ob.append(b"one\ntwo\n")
    end_job(w, ob)
    fake.script["log"] = [http(503)] * 100000
    s.start()
    try:
        s.thread.join(5)
        assert ob.done
    finally:
        s.halt.set()
    assert [c[5]["log_bytes"] for c in fake.calls if c[0] == "finish"] == [8]
    said = [x for x in w.logs if "not sent" in x]
    assert said == [
        "#7 report: the finish goes first 300s after the job ended; not sent: the last 8 bytes"
        " of the log (log_complete is false)"
    ], w.logs


def test_b8_3_b_finish_sent_first_behind_the_bundle_names_only_the_bundle(tmp_path, fast):
    """# batch-8 #3 로그는 다 갔고 산출물 묶음만 5xx 로 밀렸다. finish 가 먼저 가며 묶음만
    말한다 — 처분은 `upload_failed` 다."""
    clock = WClock()
    fake = FakeServer(clock, tick=60)
    w, ob, s = live_sender(tmp_path, fake, clock)
    bundle = tmp_path / "bundle.tar"
    bundle.write_bytes(b"tar")
    ob.bundle = {"path": str(bundle), "state": "pending", "sha256": None}
    end_job(w, ob, artifacts={"state": "ready", "file_count": 1, "total_bytes": 3})
    fake.script["upload"] = [http(503)] * 100000
    s.start()
    try:
        s.thread.join(5)
        assert ob.done
    finally:
        s.halt.set()
    fins = [c for c in fake.calls if c[0] == "finish"]
    assert [c[5]["artifacts"]["reason_code"] for c in fins] == ["upload_failed"]
    said = [x for x in w.logs if "not sent" in x]
    assert said == [
        "#7 report: the finish goes first 300s after the job ended; not sent: the bundle"
        " (upload_failed)"
    ], w.logs
