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
