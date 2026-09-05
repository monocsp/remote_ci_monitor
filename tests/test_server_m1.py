"""서버(M1) — SSE `GET /events` · `Last-Event-ID` 재생 · keep-alive · 연결 상한 503 폴백 ·
`GET /jobs/{id}/events` · HEAD 405 · `POST /api/eta` · `estimate.confidence` · `sse_connections` ·
가짜 샘플러의 `hosts[]`. 명세는 docs/m1-workplan.md 0(E) · 3 · 4절.

SSE 는 `http.client` 로 열고 줄 단위로 읽는다(chunked 든 close-delimited 든 같다). 읽기마다
남은 마감을 소켓 타임아웃으로 걸어 **타임아웃은 곧 실패**다 — 「안 온다」를 기다림으로 증명하지
않고, 뒤에 오는 이벤트가 먼저 보이는지로 증명한다.
"""

import http.client
import json
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from remote_ci_monitor.core.model import HostSample
from remote_ci_monitor.core.status import iso
from remote_ci_monitor.events import EventBus
from test_server import Server

Frame = dict[str, Any]


# ── 도우미 ───────────────────────────────────────────────────────────────────


class SseStream:
    """`GET path` 를 열어 둔 SSE 연결. `frame()` 은 다음 프레임, `comment()` 는 다음 ':' 줄."""

    def __init__(
        self,
        srv: Server,
        path: str,
        *,
        token: str | None = None,
        last_event_id: int | None = None,
        timeout: float = 10.0,
    ):
        self.conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=timeout)
        self.conn.connect()
        self.sock = self.conn.sock  # getresponse 뒤에는 conn.sock 이 None 이 된다
        headers = {"Accept": "text/event-stream"}
        if token:
            headers["Authorization"] = f"Bearer {srv.tokens.get(token, token)}"
        if last_event_id is not None:
            headers["Last-Event-ID"] = str(last_event_id)
        self.conn.request("GET", path, headers=headers)
        self.resp = self.conn.getresponse()
        self.status = self.resp.status
        self.headers = {k.lower(): v for k, v in self.resp.getheaders()}
        self.body: bytes | None = None if self.status == 200 else self.resp.read()
        self.seen: list[Frame] = []

    def close(self) -> None:
        try:
            self.sock.close()
        finally:
            self.conn.close()

    def _readline(self, deadline: float) -> bytes | None:
        """한 줄. EOF 면 None. 마감을 넘기면 AssertionError."""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(f"SSE: nothing arrived before the deadline; seen={self.seen}")
        self.sock.settimeout(remaining)
        try:
            line = self.resp.readline()
        except TimeoutError as e:
            raise AssertionError(f"SSE: timed out waiting for a line; seen={self.seen}") from e
        return line if line else None

    def frame(self, timeout: float = 5.0) -> Frame | None:
        """다음 프레임(dict: event · id · data · comments). 스트림이 닫히면 None."""
        deadline = time.monotonic() + timeout
        fr: Frame = {"event": None, "id": None, "data": None, "comments": []}
        seen_field = False
        data_lines: list[str] = []
        while True:
            line = self._readline(deadline)
            if line is None:
                if seen_field:
                    self._finish(fr, data_lines)
                    return fr
                return None
            text = line.decode("utf-8").rstrip("\r\n")
            if text == "":
                if not seen_field:
                    continue  # 주석만 있던 빈 이벤트는 건너뛴다
                self._finish(fr, data_lines)
                return fr
            if text.startswith(":"):
                fr["comments"].append(text)
                continue
            field, _, value = text.partition(":")
            value = value[1:] if value.startswith(" ") else value
            seen_field = True
            if field == "event":
                fr["event"] = value
            elif field == "id":
                fr["id"] = int(value)
            elif field == "data":
                data_lines.append(value)

    def _finish(self, fr: Frame, data_lines: list[str]) -> None:
        if data_lines:
            fr["data"] = json.loads("\n".join(data_lines))
        self.seen.append(fr)

    def comment(self, timeout: float = 5.0) -> str:
        """다음 ':' 로 시작하는 줄(keep-alive). 프레임 줄은 건너뛴다."""
        deadline = time.monotonic() + timeout
        while True:
            line = self._readline(deadline)
            if line is None:
                raise AssertionError("SSE: stream closed before a comment line")
            text = line.decode("utf-8").rstrip("\r\n")
            if text.startswith(":"):
                return text

    def until(
        self, pred: Callable[[Frame], bool], *, timeout: float = 5.0
    ) -> tuple[Frame, list[Frame]]:
        """pred 가 참인 프레임까지 읽는다. (그 프레임, 그동안 읽은 프레임 전부 — 그것 포함)."""
        deadline = time.monotonic() + timeout
        frames: list[Frame] = []
        while True:
            fr = self.frame(timeout=max(0.0, deadline - time.monotonic()))
            if fr is None:
                raise AssertionError(f"SSE: stream closed before a matching frame; got {frames}")
            frames.append(fr)
            if pred(fr):
                return fr, frames


def hello_of(stream: SseStream) -> Frame:
    fr = stream.frame()
    assert fr is not None and fr["event"] == "hello", fr
    return fr


def is_job_event(kind: str, job_id: int, state: str | None = None) -> Callable[[Frame], bool]:
    def pred(fr: Frame) -> bool:
        if fr["event"] != kind or not isinstance(fr["data"], dict):
            return False
        if fr["data"].get("job_id") != job_id:
            return False
        return state is None or fr["data"].get("state") == state

    return pred


def status_until(srv: Server, pred: Callable[[dict], bool], *, timeout: float = 3.0) -> dict:
    """`/api/status` 를 pred 가 참이 될 때까지 다시 읽는다(상태 캐시 0.2초 디바운스 허용)."""
    deadline = time.monotonic() + timeout
    while True:
        doc = srv.req("GET", "/api/status")[1]
        if pred(doc) or time.monotonic() >= deadline:
            return doc
        time.sleep(0.05)


def host_sample(now: datetime, *, age_seconds: float = 2, name: str = "macmini") -> HostSample:
    """PLAN 「/api/status 스키마 v1」 예시 모양의 표본 하나."""
    sampled = now - timedelta(seconds=age_seconds)
    return HostSample(
        name=name,
        source="local",
        sampled_at=sampled,
        interval_seconds=5,
        os="darwin",
        cores=10,
        load=(3.48, 3.1, 2.9),
        cpu={"user": 17.0, "sys": 4.0, "idle": 79.0, "busy": 21.0},
        memory={
            "total_bytes": 25_769_803_776,
            "used_bytes": 15_032_385_536,
            "compressed_bytes": 2_254_857_830,
        },
        gpu={
            "util_pct": 13,
            "mem_used_bytes": 594_411_520,
            "mem_total_bytes": None,
            "source": "ioreg",
        },
        gpu_note=None,
        top=({"comm": "dart", "cpu": 180.4, "rss_mb": 500},),
        history=(
            {
                "at": iso(sampled - timedelta(seconds=5)),
                "cpu_busy": 18.0,
                "mem_used_bytes": 14_900_000_000,
                "gpu_util_pct": 10,
            },
            {
                "at": iso(sampled),
                "cpu_busy": 21.0,
                "mem_used_bytes": 15_032_385_536,
                "gpu_util_pct": 13,
            },
        ),
    )


class StubSampler:
    """`HostSampler.latest()` 만 흉내 낸다 — (hosts, hosts_error)."""

    def __init__(self, hosts: list[HostSample], error: str | None = None):
        self.result = (hosts, error)

    def latest(self) -> tuple[list[HostSample], str | None]:
        return self.result


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path, workers=False)
    yield s
    s.close()


@pytest.fixture
def live(tmp_path):
    s = Server(tmp_path, workers=True)
    yield s
    s.close()


# ── GET /events ──────────────────────────────────────────────────────────────


def test_events_stream_hello_then_job_lifecycle(live):
    s = SseStream(live, "/events")
    try:
        assert s.status == 200
        assert s.headers["content-type"].startswith("text/event-stream")
        assert "no-store" in s.headers.get("cache-control", "")
        hello = hello_of(s)
        assert isinstance(hello["data"]["last_id"], int) and hello["data"]["last_id"] >= 0
        assert hello["data"]["server"]["version"] == live.app.version
        assert isinstance(hello["data"]["server"]["uptime_seconds"], int | float)
        assert hello["data"]["generated_at"].endswith("Z")

        jid = live.submit()[1]["job_id"]
        changed, _ = s.until(is_job_event("job_changed", jid))
        assert changed["data"]["state"] == "uploading"
        assert isinstance(changed["id"], int) and changed["id"] > hello["data"]["last_id"]

        live.upload(jid)
        finished, frames = s.until(is_job_event("job_finished", jid), timeout=10)
        assert finished["data"]["state"] == "succeeded" and finished["data"]["exit_code"] == 0
        states = [f["data"]["state"] for f in frames if is_job_event("job_changed", jid)(f)]
        assert "queued" in states and "running" in states, states
        markers = [
            (f["data"]["kind"], f["data"]["value"])
            for f in frames
            if is_job_event("marker", jid)(f)
        ]
        assert ("step", "a") in markers, markers
        ids = [f["id"] for f in s.seen if f["id"] is not None]
        assert ids == sorted(ids) and len(set(ids)) == len(ids)  # 단조 · 중복 없음
    finally:
        s.close()


def test_joiner_and_pause_publish_events(live):
    s = SseStream(live, "/events")
    try:
        hello_of(s)
        jid = live.submit()[1]["job_id"]
        s.until(is_job_event("job_changed", jid, "uploading"))
        assert live.submit(token="bob")[1]["joined"] is True  # 합류자 변경도 job_changed
        joined, _ = s.until(is_job_event("job_changed", jid))
        assert joined["data"]["state"] == "uploading"
        assert live.req("POST", "/pause", token="admin", json_body={})[0] == 200
        paused, _ = s.until(lambda f: f["event"] == "server")
        assert paused["data"]["paused"]["by"] == "macmini-admin"
        assert isinstance(paused["data"]["workers"], list)
        live.req("POST", "/resume", token="admin", json_body={})
        resumed, _ = s.until(lambda f: f["event"] == "server" and f["data"]["paused"] is None)
        assert isinstance(resumed["id"], int)
    finally:
        s.close()


def test_last_event_id_replays_missed_events(srv):
    first = SseStream(srv, "/events")
    hello_of(first)
    jid1 = srv.submit()[1]["job_id"]
    seen, _ = first.until(is_job_event("job_changed", jid1))
    seen_id = seen["id"]
    first.close()
    jid2 = srv.submit(token="bob", tree_hash="ab" * 32)[1]["job_id"]  # 아무도 안 듣는 동안
    second = SseStream(srv, "/events", last_event_id=seen_id)
    try:
        hello = hello_of(second)
        assert hello["data"]["last_id"] > seen_id
        replayed, frames = second.until(is_job_event("job_changed", jid2))
        assert replayed["data"]["state"] == "uploading"
        ids = [f["id"] for f in frames if f["id"] is not None]
        assert ids and ids == sorted(ids) and ids[0] > seen_id  # 본 것은 다시 오지 않는다
        assert not any(is_job_event("job_changed", jid1)(f) for f in frames)
    finally:
        second.close()


def test_last_event_id_outside_ring_buffer_gets_reset_then_live_events(srv):
    # 기본 링 크기는 test_events 가 고정한다. 여기서는 라우트가 reset 을 흘리는지만 본다 — 작은
    # 버스를 끼워 600개 대신 20개로 밀어낸다.
    srv.app.bus = EventBus(history=8)
    now = datetime.now(UTC)
    for n in range(20):
        srv.app.bus.publish("server", {"paused": None, "workers": [], "n": n}, at=now)
    s = SseStream(srv, "/events", last_event_id=1)
    try:
        hello = hello_of(s)
        assert hello["data"]["last_id"] == 20
        reset = s.frame()
        assert reset is not None and reset["event"] == "reset" and reset["data"] == {}
        jid = srv.submit()[1]["job_id"]
        changed, _ = s.until(is_job_event("job_changed", jid))
        assert changed["id"] > 20  # reset 뒤 실시간 이벤트는 정상
    finally:
        s.close()


def test_keepalive_comment_arrives_within_interval(tmp_path):
    srv = Server(tmp_path, workers=False, sse_keepalive_seconds=1)
    try:
        s = SseStream(srv, "/events")
        try:
            hello_of(s)
            t0 = time.monotonic()
            line = s.comment(timeout=3.5)
            assert line.startswith(":")
            assert time.monotonic() - t0 < 3.5
        finally:
            s.close()
    finally:
        srv.close()


def test_connection_cap_gives_503_with_poll_fallback(tmp_path):
    srv = Server(tmp_path, workers=False, sse_max_connections=2)
    streams: list[SseStream] = []
    try:
        for _ in range(2):
            s = SseStream(srv, "/events")
            assert s.status == 200
            hello_of(s)
            streams.append(s)
        doc = status_until(srv, lambda d: d["server"]["sse_connections"] == 2)
        assert doc["server"]["sse_connections"] == 2
        status, headers, raw = srv.req("GET", "/events", raw=True)
        assert status == 503
        assert headers.get("Retry-After") == "10"
        assert headers["Content-Type"].startswith("application/json")
        assert json.loads(raw) == {
            "error": "too many event streams",
            "fallback": "poll",
            "poll_seconds": 10,
        }
        assert srv.req("GET", "/api/status")[0] == 200  # 일반 요청은 막히지 않는다
    finally:
        for s in streams:
            s.close()
        srv.close()


def test_head_and_post_on_event_streams_are_405(srv):
    jid = srv.submit()[1]["job_id"]
    assert srv.req("HEAD", "/events", raw=True)[0] == 405
    assert srv.req("HEAD", f"/jobs/{jid}/events", raw=True)[0] == 405
    assert srv.req("POST", "/events", json_body={})[0] == 405
    assert srv.req("POST", f"/jobs/{jid}/events", json_body={})[0] == 405


def test_read_auth_basic_guards_events_and_eta(tmp_path):
    srv = Server(tmp_path, workers=False, read_auth="basic")
    try:
        denied = SseStream(srv, "/events")
        assert denied.status == 401 and b"token" in (denied.body or b"")
        denied.close()
        body = {"preset": "gate", "inputs": {"scope": "full"}}
        assert srv.req("POST", "/api/eta", json_body=body)[0] == 401
        assert srv.req("POST", "/api/eta", token="alice", json_body=body)[0] == 200
        allowed = SseStream(srv, "/events", token="alice")
        try:
            assert allowed.status == 200
            hello_of(allowed)
        finally:
            allowed.close()
    finally:
        srv.close()


# ── GET /jobs/{id}/events ────────────────────────────────────────────────────


def test_job_events_for_finished_job_sends_finished_then_closes(live):
    jid = live.submit()[1]["job_id"]
    live.upload(jid)
    live.wait_terminal(jid)
    s = SseStream(live, f"/jobs/{jid}/events")
    try:
        assert s.status == 200 and s.headers["content-type"].startswith("text/event-stream")
        hello_of(s)
        fin = s.frame()
        assert fin is not None and fin["event"] == "job_finished", fin
        assert fin["data"]["job_id"] == jid and fin["data"]["state"] == "succeeded"
        assert fin["data"]["exit_code"] == 0
        assert s.frame(timeout=5) is None  # 곧바로 닫는다
    finally:
        s.close()


def test_job_events_for_running_job_only_carry_that_job(live):
    a = live.submit(preset="slow")[1]["job_id"]
    live.upload(a)
    live.wait_state(a, "running")
    s = SseStream(live, f"/jobs/{a}/events")
    try:
        hello_of(s)
        b = live.submit(token="bob", preset="ok", tree_hash="cd" * 32)[1]["job_id"]
        assert live.upload(b, token="bob")[0] == 200  # b 의 uploading→queued 가 먼저 발행된다
        assert live.req("POST", f"/jobs/{a}/cancel", token="alice", json_body={})[0] == 200
        fin, frames = s.until(is_job_event("job_finished", a), timeout=10)
        assert fin["data"]["state"] == "cancelled"
        assert all(f["data"]["job_id"] == a for f in frames), frames  # b 는 한 번도 안 보인다
        assert all(f["event"] in ("job_changed", "job_finished", "marker") for f in frames)
        assert any(is_job_event("job_changed", a, "cancelling")(f) for f in frames), frames
    finally:
        s.close()
    live.wait_terminal(b)


def test_job_events_unknown_job_is_404(srv):
    s = SseStream(srv, "/jobs/999/events")
    try:
        assert s.status == 404
    finally:
        s.close()


# ── POST /api/eta ────────────────────────────────────────────────────────────


def test_api_eta_returns_virtual_row_position_and_ahead(srv):
    body = {"preset": "gate", "inputs": {"scope": "full"}}
    status, resp = srv.req("POST", "/api/eta", json_body=body)
    assert status == 200, resp
    assert set(resp) >= {"job", "ahead", "generated_at"}
    assert resp["generated_at"].endswith("Z")
    job = resp["job"]
    assert job["id"] is None and job["position"] == 1 and resp["ahead"] == 0
    assert job["preset"] == "gate" and job["state"] == "queued"
    assert job["reason"] == "waiting_for_lane" and job["ahead_job_id"] is None
    est = job["estimate"]
    assert est["confidence"] == "low" and est["source"] in ("preset", "default")
    assert est["expected_seconds"] == 600 and est["wait_seconds"] == 0  # 빈 레인 → 바로 시작
    assert est["finish_at"] is not None and est["finish_at"].endswith("Z")
    # 대기 잡 둘 뒤에 서면 3번째, ahead 2
    jid1 = srv.submit()[1]["job_id"]
    jid2 = srv.submit(token="bob", tree_hash="ab" * 32)[1]["job_id"]
    status, resp = srv.req("POST", "/api/eta", json_body=body)
    assert status == 200
    assert resp["job"]["position"] == 3 and resp["ahead"] == 2
    assert resp["job"]["ahead_job_id"] == jid2 and jid1 < jid2
    assert resp["job"]["estimate"]["wait_seconds"] > 0
    # inputs 를 생략하면 기본값(scope=full)
    assert srv.req("POST", "/api/eta", json_body={"preset": "gate"})[0] == 200


def test_api_eta_rejects_unknown_preset_bad_input_and_wrong_method(srv):
    status, body = srv.req("POST", "/api/eta", json_body={"preset": "nope"})
    assert status == 400 and "unknown preset" in body["error"]
    status, body = srv.req(
        "POST", "/api/eta", json_body={"preset": "gate", "inputs": {"scope": "huge"}}
    )
    assert status == 400 and "is not one of" in body["error"]
    status, body = srv.req(
        "POST", "/api/eta", json_body={"preset": "gate", "inputs": {"bogus": "1"}}
    )
    assert status == 400 and "unknown input" in body["error"]
    assert srv.req("POST", "/api/eta", json_body={})[0] == 400
    assert srv.req("POST", "/api/eta", json_body=[1, 2])[0] == 400
    assert srv.req("GET", "/api/eta")[0] == 405


def test_api_eta_when_paused_has_no_finish_time(srv):
    assert srv.req("POST", "/pause", token="admin", json_body={})[0] == 200
    body = {"preset": "gate", "inputs": {"scope": "full"}}
    status, resp = srv.req("POST", "/api/eta", json_body=body)
    assert status == 200
    job = resp["job"]
    assert job["reason"] == "paused" and job["position"] == 1
    assert job["estimate"]["finish_at"] is None and job["estimate"]["wait_seconds"] is None
    srv.req("POST", "/resume", token="admin", json_body={})
    status, resp = srv.req("POST", "/api/eta", json_body=body)
    assert resp["job"]["estimate"]["finish_at"] is not None


# ── /api/status 추가 키 ──────────────────────────────────────────────────────


def test_status_rows_carry_confidence_and_server_counts_sse(srv):
    doc = srv.req("GET", "/api/status")[1]
    assert doc["schema_version"] == 1
    assert isinstance(doc["server"]["sse_connections"], int)
    assert doc["server"]["sse_connections"] == 0
    jid = srv.submit()[1]["job_id"]
    doc = status_until(srv, lambda d: bool(d["pools"][0]["queue"]))
    row = doc["pools"][0]["queue"][0]
    assert row["id"] == jid and row["estimate"]["confidence"] == "low"
    assert row["estimate"]["source"] in ("preset", "default")
    view = srv.req("GET", f"/jobs/{jid}")[1]
    assert view["estimate"]["confidence"] == "low"  # 잡 단건 조회도 같은 행 모양
    s = SseStream(srv, "/events")
    try:
        hello_of(s)
        doc = status_until(srv, lambda d: d["server"]["sse_connections"] == 1)
        assert doc["server"]["sse_connections"] == 1
    finally:
        s.close()


def test_hosts_come_from_sampler_stub(srv):
    now = datetime.now(UTC)
    fresh = host_sample(now, age_seconds=2)
    srv.app.sampler = StubSampler([fresh])
    doc = status_until(srv, lambda d: bool(d["pools"][0]["hosts"]))
    pool = doc["pools"][0]
    assert pool["hosts_error"] is None and len(pool["hosts"]) == 1
    h = pool["hosts"][0]
    assert h["name"] == "macmini" and h["source"] == "local" and h["os"] == "darwin"
    assert h["stale"] is False and 0 <= h["age_seconds"] <= 10 and h["interval_seconds"] == 5
    assert h["sampled_at"] == iso(fresh.sampled_at)
    assert h["cpu"]["busy"] == 21.0 and h["load"] == [3.48, 3.1, 2.9] and h["cores"] == 10
    assert h["memory"]["used_bytes"] == 15_032_385_536
    assert h["gpu"] == {
        "util_pct": 13,
        "mem_used_bytes": 594_411_520,
        "mem_total_bytes": None,
        "source": "ioreg",
    }
    assert h["gpu_note"] is None and h["top"] == [{"comm": "dart", "cpu": 180.4, "rss_mb": 500}]
    assert h["history"] == [dict(x) for x in fresh.history]
    assert {"at", "cpu_busy", "mem_used_bytes", "gpu_util_pct"} == set(h["history"][0])
    # 오래된 표본은 stale (3 × interval 초과)
    srv.app.sampler = StubSampler([replace(fresh, sampled_at=now - timedelta(seconds=60))])
    doc = status_until(
        srv, lambda d: bool(d["pools"][0]["hosts"]) and d["pools"][0]["hosts"][0]["stale"]
    )
    assert doc["pools"][0]["hosts"][0]["stale"] is True
    assert doc["pools"][0]["hosts"][0]["age_seconds"] >= 60
    # 수집 실패는 null + hosts_error (빈 목록이 아니다)
    srv.app.sampler = StubSampler([], "sampler: boom")
    doc = status_until(srv, lambda d: d["pools"][0]["hosts_error"] == "sampler: boom")
    assert doc["pools"][0]["hosts"] is None
    assert doc["pools"][0]["hosts_error"] == "sampler: boom"
    # 아직 표본이 없으면 [] 이고 오류가 아니다
    srv.app.sampler = StubSampler([])
    doc = status_until(
        srv, lambda d: d["pools"][0]["hosts"] == [] and d["pools"][0]["hosts_error"] is None
    )
    assert doc["pools"][0]["hosts"] == [] and doc["pools"][0]["hosts_error"] is None
