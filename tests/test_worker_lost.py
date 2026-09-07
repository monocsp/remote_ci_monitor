"""워커 down · lost 판정(M5b-2 §4) — timeout 을 넘긴 워커의 잡은 lost, 워커는 down, 다시
heartbeat 이 오면 up. 서버 재시작은 로컬 잡만 lost 로 만들고 원격 잡은 timeout 뒤에 판정한다.
`pools[].lanes` 는 살아 있는 레인 합, `server.workers[]` 는 로컬 먼저 · 원격은 이름순.
취소 미확인(heartbeat 은 오는데 finish 가 없다)은 cancelled, 닿지 않으면 lost 가 우선한다.

명세는 docs/m5b2-workplan.md §3(취소 · 서버 재시작) · §4 · §6. 구현 전이라 빨간 것이 정상이다.

test_worker_api.WorkerServer(in-process HTTP + 주입 시계)를 그대로 쓴다. janitor 루프(5초)는
기다리지 않고 `app.mark_lost_workers(now)` 를 직접 부른다(§6). 벽시계 sleep 없음.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from remote_ci_monitor.core.model import (
    CANCELLED,
    CANCELLING,
    DEFAULT_POOL,
    LOST,
    QUEUED,
    RUNNING,
)
from remote_ci_monitor.core.status import iso
from remote_ci_monitor.events import KIND_JOB_FINISHED, Event
from remote_ci_monitor.server import App
from test_worker_api import GRACE, HEARTBEAT, SAMPLE, T0, TIMEOUT, WorkerServer, at


@pytest.fixture
def srv(tmp_path):
    s = WorkerServer(tmp_path)
    yield s
    s.close()


def drain(sub) -> list[Event]:
    """구독 큐에 쌓인 이벤트 전부(비어 있으면 0.05초 안에 돌아온다)."""
    out: list[Event] = []
    while (ev := sub.get(timeout=0.05)) is not None:
        out.append(ev)
    return out


def finished_events(events: list[Event]) -> list[dict]:
    return [e.data for e in events if e.kind == KIND_JOB_FINISHED]


def states_of(srv: WorkerServer, name: str) -> list[str]:
    return [w["state"] for w in srv.workers() if w["worker"] == name]


# ── timeout → lost · down · 다시 up ──────────────────────────────────────────


def test_worker_past_timeout_loses_its_running_job_and_shows_down(srv):
    """§4: `now − last_seen_at > worker_timeout_seconds` 인 워커의 running 잡 → lost + summary
    `worker <name> unreachable for <N>s`(N = 서버가 그 워커의 요청을 마지막으로 받은 뒤 지난 초)
    + `job_finished` 이벤트. 워커 행은 남아 `server.workers[]` 에 down 으로, 그 풀의 `lanes` 는
    살아 있는 레인 합(로컬 + 살아 있는 원격)이라 줄고, 그 풀의 queued 행은 `worker_down` 이 된다.
    두 번 불러도 더 없다."""
    a = srv.queued_job()
    b = srv.queued_job(token="bob")
    c = srv.queued_job(token="admin", preset="lin")
    srv.registered("build-02", lanes=2)
    srv.registered("lin-01", pool="linux")
    srv.clock.advance(10)
    assert srv.claimed("build-02", 1) == a
    assert srv.heartbeat("build-02", jobs=[a])[0] == 200  # 마지막 접촉 = T0 + 10
    # 살아 있을 때
    pools = srv.pools()
    assert pools["default"]["lanes"] == 1 + 2 and pools["linux"]["lanes"] == 1
    assert srv.worker_lane("build-02", 1)["state"] == "busy"
    assert srv.worker_lane("build-02", 1)["since"] == iso(at(10))  # busy 면 잡 started_at
    assert srv.worker_lane("build-02", 2)["since"] == iso(T0)  # idle 이면 registered_at
    assert srv.row(c)["reason"] in ("waiting_for_lane", "not_scheduled")
    assert srv.app.mark_lost_workers(srv.clock.now) == []
    # 그 뒤 heartbeat 없이 80초(timeout 60 을 넘겼다)
    srv.clock.advance(80)
    assert states_of(srv, "build-02") == ["down", "down"]
    assert states_of(srv, "lin-01") == ["down"]
    sub = srv.app.bus.subscribe()
    try:
        assert srv.app.mark_lost_workers(srv.clock.now) == [a]
        events = drain(sub)
    finally:
        srv.app.bus.unsubscribe(sub)
    j = srv.store.get_job(a)
    assert j.state == LOST and j.summary == "worker build-02 unreachable for 80s"
    assert j.finished_at == at(90) and j.lane is None and j.exit_code is None
    assert {"job_id": a, "state": LOST, "exit_code": None} in finished_events(events)
    assert srv.store.get_job(b).state == QUEUED and srv.store.get_job(c).state == QUEUED
    lane = srv.worker_lane("build-02", 1)
    assert lane["state"] == "down" and lane["job_id"] is None
    assert lane["worker"] == "build-02" and lane["display_name"] == "build-02/1"
    pools = srv.pools()
    assert pools["default"]["lanes"] == 1 and pools["linux"]["lanes"] == 0
    assert srv.row(b)["reason"] in ("waiting_for_lane", "not_scheduled")  # 로컬 레인은 산다
    assert srv.row(c)["reason"] == "worker_down" and srv.row(c)["estimate"]["finish_at"] is None
    assert [r["id"] for r in pools["default"]["recent"]] == [a]
    assert srv.app.mark_lost_workers(srv.clock.now) == []  # 더 없다
    assert srv.store.get_job(a).summary == "worker build-02 unreachable for 80s"


def test_heartbeat_brings_a_down_worker_back_up_without_resuming_the_lost_job(srv):
    """§4: 다시 heartbeat 이 오면 up(idle) — lanes 와 hosts 가 돌아온다. lost 로 닫힌 잡은
    이어받지 않는다(재현성 — 새로 제출). down 인 동안 그 풀의 `hosts[]` 는 비어 있다."""
    a = srv.queued_job(preset="lin")
    srv.registered("lin-01", pool="linux", lanes=2)
    assert srv.claimed("lin-01", 1) == a
    assert srv.heartbeat("lin-01", jobs=[a], host_sample=SAMPLE)[0] == 200
    assert [h["name"] for h in srv.pools()["linux"]["hosts"]] == ["lin-01"]
    srv.clock.advance(TIMEOUT + 30)
    assert srv.app.mark_lost_workers(srv.clock.now) == [a]
    assert srv.pools()["linux"]["lanes"] == 0 and srv.pools()["linux"]["hosts"] == []
    assert states_of(srv, "lin-01") == ["down", "down"]
    assert srv.heartbeat("lin-01", jobs=[a], host_sample=SAMPLE)[0] == 200  # 워커는 a 를 아직 안다
    assert states_of(srv, "lin-01") == ["idle", "idle"]
    assert srv.pools()["linux"]["lanes"] == 2
    assert [h["name"] for h in srv.pools()["linux"]["hosts"]] == ["lin-01"]
    j = srv.store.get_job(a)
    assert j.state == LOST and j.summary == f"worker lin-01 unreachable for {TIMEOUT + 30}s"
    assert srv.app.mark_lost_workers(srv.clock.now) == []
    b = srv.queued_job(token="bob", preset="lin")
    assert srv.claimed("lin-01", 1) == b  # 레인은 비어 있다
    # 늦게 온 a 의 finish 는 409 — lost 가 그대로다
    status, body = srv.finish("lin-01", a, "succeeded", exit_code=0)
    assert status == 409 and body["error"] == f"job #{a} is lost"
    assert srv.store.get_job(a).state == LOST


# ── 취소와 lost 의 우선순위 (§3 취소) ────────────────────────────────────────


def test_cancelling_job_of_a_down_worker_becomes_lost_not_cancelled(srv):
    """§3(리뷰 반영 — 두 규칙의 우선순위): 워커가 닿지 않으면 cancelling 잡도 `lost` 가 우선한다
    — `kill_at + 2×heartbeat` 가 지나도 cancelled 가 아니다."""
    a = srv.queued_job()
    srv.registered("build-02")
    assert srv.claimed("build-02") == a
    assert srv.cancel(a)[1]["state"] == CANCELLING  # kill_at = T0 + GRACE
    srv.clock.advance(TIMEOUT + 30)  # kill_at + 2×heartbeat 도, timeout 도 넘겼다
    assert srv.app.mark_lost_workers(srv.clock.now) == [a]
    j = srv.store.get_job(a)
    assert j.state == LOST and j.summary == f"worker build-02 unreachable for {TIMEOUT + 30}s"
    assert srv.worker_lane("build-02", 1)["state"] == "down"


def test_unconfirmed_cancel_of_a_live_worker_is_closed_as_cancelled(srv):
    """§3 취소: 워커가 **heartbeat 은 계속 보내면서** `kill_at + 2 × heartbeat_seconds` 가 지나도록
    finish 를 안 보내면 서버가 `cancelled` 로 닫는다(heartbeat 처리 중이든 janitor 진입점
    `mark_lost_workers` 든) — 요약 `worker did not confirm the cancel`, `cancelled_by` 는 취소한
    사람. 그 전엔 cancelling 그대로다."""
    a = srv.queued_job()
    b = srv.queued_job(token="bob")
    srv.registered("build-02")
    assert srv.claimed("build-02") == a
    assert srv.cancel(a)[1]["state"] == CANCELLING  # kill_at = T0 + GRACE
    kill_at = T0 + timedelta(seconds=GRACE)
    assert srv.row(a)["cancel"]["kill_at"] == iso(kill_at)
    srv.clock.advance(HEARTBEAT)
    assert srv.heartbeat("build-02", jobs=[a])[1]["cancel"] == [a]
    srv.clock.advance(GRACE)  # = kill_at + heartbeat: 아직 아니다
    assert srv.heartbeat("build-02", jobs=[a])[0] == 200
    assert srv.app.mark_lost_workers(srv.clock.now) == []
    assert srv.store.get_job(a).state == CANCELLING
    srv.clock.advance(HEARTBEAT + 1)  # kill_at + 2×heartbeat + 1
    sub = srv.app.bus.subscribe()  # heartbeat 처리 중이든 janitor 진입점이든 — 둘 다 여기서 본다
    try:
        assert srv.heartbeat("build-02", jobs=[a])[0] == 200  # 여전히 살아 있다
        assert srv.app.mark_lost_workers(srv.clock.now) == []  # lost 는 아니다
        events = drain(sub)
    finally:
        srv.app.bus.unsubscribe(sub)
    j = srv.store.get_job(a)
    assert j.state == CANCELLED and j.summary == "worker did not confirm the cancel"
    assert j.cancelled_by == "alice-laptop" and j.finished_at == srv.clock.now
    assert {"job_id": a, "state": CANCELLED, "exit_code": None} in finished_events(events)
    assert srv.worker_lane("build-02", 1)["state"] == "idle"
    assert srv.heartbeat("build-02", jobs=[])[1]["cancel"] == []
    assert srv.claimed("build-02") == b  # 레인이 비었다
    status, body = srv.finish("build-02", a, "cancelled", exit_code=-9)  # 늦은 확인은 409
    assert status == 409 and body["error"] == f"job #{a} is cancelled"


# ── 서버 재시작 (§3 서버 재시작) ─────────────────────────────────────────────


def test_server_restart_keeps_remote_jobs_and_loses_local_ones(srv):
    """§3(리뷰 반영): `recover_on_start` 는 **로컬 잡만**(`worker_name IS NULL`) lost 로 만든다.
    원격 워커의 running 잡은 그대로 running 이고 레인 상태도 DB 로 다시 계산된다(busy). 그 뒤
    `last_seen_at` 이 timeout 을 넘기면 `mark_lost_workers` 가 닫는다."""
    remote = srv.queued_job()
    local = srv.queued_job(token="bob")
    srv.registered("build-02")
    assert srv.claimed("build-02") == remote
    claimed = srv.store.claim(1, srv.clock.now)  # 로컬 레인 1 이 잡은 것처럼(worker_name 없음)
    assert claimed is not None and claimed.id == local
    assert srv.store.get_job(remote).worker_name == "build-02"
    assert srv.store.get_job(local).worker_name is None
    srv.clock.advance(20)
    app2 = App(srv.cfg, srv.store, now_fn=srv.clock)  # 같은 저장소로 다시 뜬 서버
    app2.start()
    try:
        j_local = srv.store.get_job(local)
        assert j_local.state == LOST and j_local.summary.startswith("server restarted")
        j_remote = srv.store.get_job(remote)
        assert j_remote.state == RUNNING and j_remote.worker_name == "build-02"
        assert j_remote.lane == 1
        doc = app2.status(None)
        lanes = [w for w in doc["server"]["workers"] if w["worker"] == "build-02"]
        assert [(w["state"], w["job_id"], w["display_name"]) for w in lanes] == [
            ("busy", remote, "build-02/1")
        ]
        assert app2.mark_lost_workers(srv.clock.now) == []  # 아직 20초 — 살아 있다
        srv.clock.advance(TIMEOUT - 20 + 1)  # 마지막 heartbeat(등록) 뒤 61초
        assert app2.mark_lost_workers(srv.clock.now) == [remote]
        j_remote = srv.store.get_job(remote)
        assert j_remote.state == LOST
        assert j_remote.summary == f"worker build-02 unreachable for {TIMEOUT + 1}s"
        doc = app2.status(None)
        assert [w["state"] for w in doc["server"]["workers"] if w["worker"] == "build-02"] == [
            "down"
        ]
    finally:
        app2.shutdown()


# ── pools[].lanes · server.workers[] 순서 · health (§4) ──────────────────────


def test_pool_lanes_are_local_plus_live_remote_lanes_and_workers_are_ordered(srv, tmp_path):
    """§4: `pools[].lanes` = 그 풀의 살아 있는 워커 레인 합(기본 풀은 로컬 + 원격 default 워커).
    `server.workers[]` 는 로컬 레인(worker null · display_name null)이 먼저, 원격은 워커 이름순 ·
    레인순. `server.lanes` 는 로컬 레인 수 그대로."""
    s = WorkerServer(tmp_path / "two", lanes=2)
    try:
        s.registered("lin-01", pool="linux", lanes=4)
        s.registered("build-03", lanes=1)
        s.registered("build-02", lanes=3)
        doc = s.status()
        assert doc["server"]["lanes"] == 2
        pools = {p["name"]: p for p in doc["pools"]}
        assert pools["default"]["lanes"] == 2 + 3 + 1 and pools["linux"]["lanes"] == 4
        assert [(w["worker"], w["lane"], w["display_name"]) for w in doc["server"]["workers"]] == [
            (None, 1, None),
            (None, 2, None),
            ("build-02", 1, "build-02/1"),
            ("build-02", 2, "build-02/2"),
            ("build-02", 3, "build-02/3"),
            ("build-03", 1, "build-03/1"),
            ("lin-01", 1, "lin-01/1"),
            ("lin-01", 2, "lin-01/2"),
            ("lin-01", 3, "lin-01/3"),
            ("lin-01", 4, "lin-01/4"),
        ]
        assert all(w["state"] == "idle" for w in doc["server"]["workers"])
        s.clock.advance(TIMEOUT + 1)
        assert s.heartbeat("build-03")[0] == 200  # build-03 만 살아남는다
        doc = s.status()
        pools = {p["name"]: p for p in doc["pools"]}
        assert pools["default"]["lanes"] == 2 + 1 and pools["linux"]["lanes"] == 0
        assert [w["state"] for w in doc["server"]["workers"]] == [
            "idle",
            "idle",
            "down",
            "down",
            "down",
            "idle",
            "down",
            "down",
            "down",
            "down",
        ]
        assert doc["server"]["lanes"] == 2
    finally:
        s.close()


def test_health_lists_pools_whose_registered_workers_are_all_down(srv):
    """§4: 등록된 워커가 전부 down 인 풀이 있으면 `/api/health` 는 200 이지만 본문에
    `pools_without_workers: [...]`(정보). 503 은 아니다(로컬은 살아 있다) — `ok` 는 true,
    `workers_down`(로컬 레인)은 빈 목록. 하나라도 살아 있으면 그 풀은 빠진다. 기본 풀은 로컬
    레인이 있어 원격 default 워커가 다 죽어도 오르지 않는다."""
    srv.worker_token("lin-02")
    srv.registered("lin-01", pool="linux")
    srv.registered("lin-02", pool="linux")
    srv.registered("build-02")
    status, body = srv.req("GET", "/api/health")
    assert status == 200 and body["ok"] is True
    assert body["pools_without_workers"] == [] and body["workers_down"] == []
    srv.clock.advance(TIMEOUT + 1)
    status, body = srv.req("GET", "/api/health")
    assert status == 200, body
    assert body["ok"] is True and body["db"] is True and "error" not in body
    assert body["pools_without_workers"] == ["linux"] and body["workers_down"] == []
    assert srv.heartbeat("lin-02")[0] == 200  # 둘 중 하나만 살아도 풀은 산다
    status, body = srv.req("GET", "/api/health")
    assert status == 200 and body["pools_without_workers"] == []
    assert srv.pools()["linux"]["lanes"] == 1
    assert srv.pools()["default"]["lanes"] == 1  # build-02 는 down — 로컬 레인만
    assert srv.pools()["default"]["name"] == DEFAULT_POOL
