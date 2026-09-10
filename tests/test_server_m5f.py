"""부하 게이트의 서버 배선(M5f §4.5) — 락 · 쿨다운 기록 · 슬롯 순서 · 한 번만 판정.

시나리오는 `docs/m5f-test-scenarios-a.md`(판정)와 `docs/m5f-workplan.md` §4.5. 여기서는 **판정
자체가 아니라 배선**을 잠근다: 레인 넷이 같은 순간에 깨어도 게이트를 지나는 admission 이 쿨다운
창당 하나인 것, 레인 1 이 락을 안 기다리는 것, 보류 레인이 long-poll 슬롯을 안 잡는 것.

타이밍 단언은 쓰지 않는다(CI 에서 흔들린다) — 횟수와 상태로 잠근다.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest

from remote_ci_monitor.core.admission import Hold
from remote_ci_monitor.core.model import HostSample
from test_worker_api import WorkerServer

NOW = datetime(2026, 9, 8, 12, 0, 0, tzinfo=UTC)


def sample(busy: float, *, at_: datetime = NOW, n: int = 3) -> HostSample:
    hist = tuple(
        {
            "at": (at_ - timedelta(seconds=5 * (n - 1 - i))).isoformat().replace("+00:00", "Z"),
            "cpu_busy": busy,
        }
        for i in range(n)
    )
    return HostSample(
        name="macmini",
        source="local",
        sampled_at=at_,
        interval_seconds=5.0,
        cpu={"busy": busy},
        history=hist,
    )


@pytest.fixture
def srv(tmp_path):
    s = WorkerServer(tmp_path, lanes=4, admission="load")
    yield s
    s.close()


class StubSampler:
    def __init__(self, host: HostSample | None):
        self.host = host

    def latest(self):
        return ([self.host], None) if self.host else ([], None)


def feed_worker(srv: WorkerServer, worker: str, busy: float) -> None:
    """워커 표본을 heartbeat 로 넣는다 — 서버가 받은 시각으로 sampled_at 을 다시 찍는다."""
    now = srv.app.now_fn()
    doc = {
        "interval_seconds": 5.0,
        "cpu": {"busy": busy},
        "history": [
            {
                "at": (now - timedelta(seconds=5 * (2 - i))).isoformat().replace("+00:00", "Z"),
                "cpu_busy": busy,
            }
            for i in range(3)
        ],
    }
    status, _ = srv.heartbeat(worker, host_sample=doc)
    assert status == 200


def feed(srv: WorkerServer, busy: float | None) -> None:
    srv.app.sampler = StubSampler(sample(busy, at_=srv.app.now_fn()) if busy is not None else None)


# ── 락 — 같은 순간에 깨어도 하나만 지난다 ───────────────────────────────────


def test_a_burst_of_lanes_passes_only_one_gated_admission(srv):
    """락이 없으면 레인 넷이 같은 `last_admit` 과 같은 창을 읽고 전부 통과한다(실측 20/20)."""
    feed(srv, 10.0)
    for i in range(4):
        srv.queued_job(tree_hash=f"{i:02x}" * 32)
    now = srv.app.now_fn()
    got: list[object] = []
    ready = threading.Barrier(3)

    def gated(lane: int):
        ready.wait()
        job, _hold = srv.app.admit(lane, None, now, lambda: srv.app.store.claim(lane, now))
        if job is not None:
            got.append(job)

    threads = [threading.Thread(target=gated, args=(lane,)) for lane in (2, 3, 4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert len(got) == 1, got  # 쿨다운 창 하나에 게이트를 지나는 admission 은 하나


def test_lane_one_never_waits_for_the_gate(srv):
    """레인 1 은 판정도 락도 안 지난다 — 큐는 어떤 부하에서도 움직여야 한다."""
    feed(srv, 99.0)  # 상한을 한참 넘었다
    srv.queued_job()
    now = srv.app.now_fn()
    job, hold = srv.app.admit(1, None, now, lambda: srv.app.store.claim(1, now))
    assert job is not None and hold is None


def test_lane_one_records_the_cooldown_even_though_it_skips_the_check(srv):
    """안 그러면 레인 1 이 무거운 잡을 집은 직후 레인 2 가 「잡 시작 전」 표본을 보고 통과한다."""
    feed(srv, 10.0)
    srv.queued_job(tree_hash="aa" * 32)
    srv.queued_job(tree_hash="bb" * 32)
    now = srv.app.now_fn()
    assert srv.app.admit(1, None, now, lambda: srv.app.store.claim(1, now))[0] is not None
    job, hold = srv.app.admit(2, None, now, lambda: srv.app.store.claim(2, now))
    assert job is None and isinstance(hold, Hold) and hold.code == "cooldown"


def test_an_empty_queue_does_not_burn_the_cooldown(srv):
    """헛돈 것은 쿨다운을 쓰지 않는다 — 안 그러면 큐가 빌 때마다 30초가 날아간다."""
    feed(srv, 10.0)
    now = srv.app.now_fn()
    assert srv.app.admit(2, None, now, lambda: None)[0] is None
    srv.queued_job()
    job, hold = srv.app.admit(2, None, now, lambda: srv.app.store.claim(2, now))
    assert job is not None and hold is None


# ── 한 번만 판정한다 ────────────────────────────────────────────────────────


def test_the_status_path_reads_the_decision_instead_of_making_it_again(srv):
    """두 번 부르면 화면과 실제가 어긋난다. 상태 경로는 claim 경로가 남긴 것을 읽는다."""
    feed(srv, 99.0)
    now = srv.app.now_fn()
    assert srv.app.hold_of(2, None, now) == (None, None)  # 아직 아무도 안 지났다
    srv.app.admit(2, None, now, lambda: None)
    hold, since = srv.app.hold_of(2, None, now)
    assert hold is not None and hold.code == "cpu_busy" and since == now


def test_held_since_survives_a_repeated_hold(srv):
    """같은 이유로 계속 막혀 있으면 「언제부터」가 되감기면 안 된다."""
    feed(srv, 99.0)
    first = srv.app.now_fn()
    srv.app.admit(2, None, first, lambda: None)
    srv.app.admit(2, None, first + timedelta(seconds=5), lambda: None)
    _hold, since = srv.app.hold_of(2, None, first)
    assert since == first


# ── 표본이 없으면 닫는다 (fail-open 금지) ───────────────────────────────────


def test_no_sample_holds_lanes_two_and_up_but_not_lane_one(srv):
    feed(srv, None)
    srv.queued_job()
    now = srv.app.now_fn()
    assert srv.app.admit(2, None, now, lambda: None)[1].code == "no_sample"
    assert srv.app.admit(1, None, now, lambda: srv.app.store.claim(1, now))[0] is not None


# ── 원격 레인 — 보류는 204 이고 슬롯을 안 잡는다 ────────────────────────────


def test_a_held_remote_lane_gets_204_without_taking_a_long_poll_slot(srv):
    """보류 레인이 8개뿐인 슬롯을 먹으면 정작 열린 레인이 즉시 204 를 받고 1초 폴링으로 떨어진다."""
    feed(srv, 99.0)
    srv.registered("build-02", lanes=4)
    srv.queued_job()
    free_before = srv.app._claim_slots._value
    status, body = srv.claim("build-02", 2, wait_seconds=5)
    assert status == 204 and body is None
    assert srv.app._claim_slots._value == free_before  # 슬롯을 안 잡았다


def test_a_held_remote_lane_shows_as_held_in_the_status(srv):
    """원격 레인의 표본은 **heartbeat 로** 온다 — 로컬 샘플러를 먹여도 그 워커와는 무관하다."""
    srv.registered("build-02", lanes=2)
    feed_worker(srv, "build-02", 99.0)
    srv.queued_job()
    assert srv.claim("build-02", 2)[0] == 204
    lane = srv.worker_lane("build-02", 2)
    assert lane["state"] == "held" and lane["hold_code"] == "cpu_busy"
    assert lane["hold_detail"] == {"cpu_busy": 99.0} and lane["held_since"] is not None
