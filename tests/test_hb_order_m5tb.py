"""heartbeat 의 순서 뒤바꿈(M5t-b 결정 335 · 339-a · 339-b · RL50) — 실제 HTTP 와 TCP 중계로.

서버는 in-process(`test_worker_api.WorkerServer`, 주입 시계)다. 중계(`tests/tcp_relay.py`)가
heartbeat 하나를 붙잡았다가 다음 heartbeat 가 닿은 뒤에 서버로 넘긴다. 늦게 닿은 heartbeat 의
`jobs` 는 받아들인 `seq` 이하라 「없음」 판정에 쓰이지 않는다. 중계 자체의 세 고장(분단 · 거부 ·
붙잡기)도 여기서 잠근다.
"""

from __future__ import annotations

import http.client
import threading
import time
from typing import Any

import pytest

from remote_ci_monitor.core.model import RUNNING
from tcp_relay import TcpRelay
from test_worker_api import WorkerServer
from test_worker_session_m5tb import PRESETS, hold_rows, job_of


@pytest.fixture
def srv(tmp_path):
    s = WorkerServer(tmp_path, presets=PRESETS)
    yield s
    s.close()


@pytest.fixture
def relay(srv):
    r = TcpRelay(srv.port)
    yield r
    r.close()


def via_relay(srv: WorkerServer, relay: TcpRelay, call: Any) -> tuple[threading.Thread, dict]:
    """`call()` 을 중계를 거쳐 다른 스레드에서 보낸다. 붙잡히면 돌아오고 포트는 되돌린다."""
    out: dict[str, Any] = {}
    real = srv.port
    srv.port = relay.port
    t = threading.Thread(target=lambda: out.setdefault("answer", call()), daemon=True)
    t.start()
    try:
        assert relay.wait_held(5), "the relay did not hold the request"
    finally:
        srv.port = real
    return t, out


def test_rl50_a_held_heartbeat_that_lands_after_the_next_one_does_not_close_the_job(srv, relay):
    srv.registered("build-02")
    jid = srv.queued_job(preset="ship")
    assert srv.claimed("build-02", 1) == jid
    settled = job_of(srv, jid).claim_seq
    # A: X 를 모르는 목록(claim 응답을 받기 전에 찍었다) — 중계가 붙잡는다
    relay.hold_next("/worker/heartbeat")
    t, out = via_relay(
        srv, relay, lambda: srv.heartbeat("build-02", jobs=[], seq=10, claims_settled=settled)
    )
    # B: 다음 heartbeat 가 먼저 닿는다
    assert srv.heartbeat("build-02", jobs=[jid], seq=11, claims_settled=settled)[0] == 200
    relay.release_held()
    t.join(10)
    assert out["answer"][0] == 200  # heard 는 오른다 — 응답은 정상이다
    assert job_of(srv, jid).state == RUNNING
    assert hold_rows(srv) == []
    # 같은 그룹 둘째 잡은 claim 되지 않는다(두 프로세스가 같이 산 구간 0)
    srv.registered("build-03")
    srv.queued_job(preset="ship")
    assert srv.claim("build-03", 1)[0] == 204


def test_rl50_the_held_heartbeat_with_a_newer_seq_still_judges(srv, relay):
    """대조군: 붙잡힌 것이 더 새 seq 라면 그 `jobs` 로 판정한다 — 위 시험이 seq 덕분에 초록이다."""
    srv.registered("build-02")
    jid = srv.queued_job(preset="ship")
    assert srv.claimed("build-02", 1) == jid
    settled = job_of(srv, jid).claim_seq
    relay.hold_next("/worker/heartbeat")
    t, out = via_relay(
        srv, relay, lambda: srv.heartbeat("build-02", jobs=[], seq=12, claims_settled=settled)
    )
    assert srv.heartbeat("build-02", jobs=[jid], seq=11, claims_settled=settled)[0] == 200
    relay.release_held()
    t.join(10)
    assert out["answer"][0] == 200
    assert job_of(srv, jid).state != RUNNING  # 「없음」 판정 — 표식 잡이라 홀드가 걸린다
    assert [h["job_id"] for h in hold_rows(srv)] == [jid]


# ── 중계 자체 ──────────────────────────────────────────────────────────────────


def _get(port: int, timeout: float) -> int:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("GET", "/api/health")
        return conn.getresponse().status
    finally:
        conn.close()


def test_the_relay_passes_bytes_pauses_refuses_and_resumes(srv, relay):
    assert _get(relay.port, 5) == 200
    relay.pause()
    t0 = time.monotonic()
    with pytest.raises(OSError):  # 분단 — 요청은 시한까지 매달린다
        _get(relay.port, 0.5)
    assert time.monotonic() - t0 >= 0.4
    relay.resume()
    assert _get(relay.port, 5) == 200
    relay.stop()
    with pytest.raises(ConnectionRefusedError):  # 거부 — 바로 실패한다
        _get(relay.port, 5)
    relay.resume()
    assert _get(relay.port, 5) == 200
