"""`WorkerClient`(M5b-3 §3) — 원격 워커가 서버의 `/worker/*` 를 부르는 얇은 클라이언트.

명세는 docs/m5b3-workplan.md §3: `WorkerClient(server, token)` 에 `register ·
claim(lane, wait_seconds)(204 → None) · download_tree(job_id, dest)(스트리밍, Content-Length 검증) ·
phase · log(job_id, data) · finish · heartbeat(jobs, sample)`. 기존 `Client._request` 를 재사용하고
**재시도는 없다**(재시도는 워커 루프의 정책). 오류는 `ClientError(status, message)`. 서버 쪽 규칙은
docs/m5b2-workplan.md §3.

- 행복 경로와 상태 코드 매핑(401 · 403 · 404 · 409)은 **진짜 in-process 서버**
  (`test_worker_api.WorkerServer`)로 잠근다 — 가짜 서버로 잠그면 서버와 어긋난 채 초록일 수 있다.
- 짧은 본문(Content-Length 보다 적게 보내고 끊는 서버) · 503 즉시 실패 · 요청 횟수는 최소 가짜
  서버로.
- 구현 전이라 빨간 것이 정상이다. `WorkerClient` 는 모듈 속성으로 찾는다(`client_mod.WorkerClient`)
  — 아직 없으면 수집 오류 대신 시험마다 빨갛고, 픽스처(서버 띄우기·닫기)는 그대로 돈다.
- 벽시계 sleep 없음. 시간 상한(초 단위)은 「블로킹하지 않는다」를 잠그는 용도다.
"""

from __future__ import annotations

import http.server
import re
import socket
import threading
import time
from typing import Any

import pytest

from remote_ci_monitor import __version__
from remote_ci_monitor import client as client_mod
from remote_ci_monitor.client import ClientError
from remote_ci_monitor.core.model import (
    CANCELLING,
    FAILED,
    LOST,
    RUNNING,
    SUCCEEDED,
)
from test_server import TAR
from test_worker_api import (
    CLAIM_WAIT,
    HEARTBEAT,
    SAMPLE,
    TIMEOUT,
    WORKER_TOKEN_REQUIRED,
    WorkerServer,
    tar_members,
)

WORKER = "build-02"


# ── 도우미 ───────────────────────────────────────────────────────────────────


def url_of(srv: WorkerServer) -> str:
    return f"http://127.0.0.1:{srv.port}"


def make_client(srv: WorkerServer, who: str = WORKER):
    """`WorkerClient(server, token)` — 위치 인자 둘(§3 `WorkerClient(server, token)`)."""
    return client_mod.WorkerClient(url_of(srv), srv.tokens[who])


def register(wc, *, pool: str = "default", lanes: int = 1) -> dict[str, Any]:
    return wc.register(pool=pool, lanes=lanes, host_name="b02.local", version=__version__)


def claimed(srv: WorkerServer, wc, jid: int, lane: int = 1) -> dict[str, Any]:
    """claim 한 번 — 그 잡이 와야 한다. payload 를 돌려준다."""
    payload = wc.claim(lane=lane, wait_seconds=0)
    assert payload is not None and payload["job"]["id"] == jid, payload
    return payload


def free_port() -> int:
    """아무도 듣지 않는 포트 — 잠깐 bind 했다 놓는다."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Scripted(http.server.BaseHTTPRequestHandler):
    """무엇을 받든 `server.script = (status, headers, body, declared_length)` 대로 답하고 끊는다.

    `declared_length` 가 실제 `body` 보다 크면 **짧은 본문**이 된다(HTTP/1.0 이라 응답 뒤 연결을
    닫는다).
    """

    protocol_version = "HTTP/1.0"

    def log_message(self, *args: Any) -> None:  # 시험 출력을 더럽히지 않는다
        pass

    def _reply(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        self.server.hits.append((self.command, self.path))  # type: ignore[attr-defined]
        self.server.requests.append(dict(self.headers))  # type: ignore[attr-defined]
        status, headers, body, declared = self.server.script  # type: ignore[attr-defined]
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body) if declared is None else declared))
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()

    do_GET = _reply
    do_POST = _reply


class FakeServer:
    def __init__(self, script: tuple[int, dict[str, str], bytes, int | None]):
        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), _Scripted)
        self.httpd.script = script  # type: ignore[attr-defined]
        self.httpd.hits = []  # type: ignore[attr-defined]
        self.httpd.requests = []  # type: ignore[attr-defined]
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self.thread.start()

    @property
    def hits(self) -> list[tuple[str, str]]:
        return self.httpd.hits  # type: ignore[attr-defined]

    @property
    def requests(self) -> list[dict[str, str]]:
        """받은 요청의 헤더(요청 순서)."""
        return self.httpd.requests  # type: ignore[attr-defined]

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def srv(tmp_path):
    s = WorkerServer(tmp_path)
    yield s
    s.close()


@pytest.fixture
def fake():
    """`use(script) -> FakeServer` — 시험이 끝나면 닫는다."""
    made: list[FakeServer] = []

    def use(script: tuple[int, dict[str, str], bytes, int | None]) -> FakeServer:
        f = FakeServer(script)
        made.append(f)
        return f

    yield use
    for f in made:
        f.close()


# ── 생성 ─────────────────────────────────────────────────────────────────────


def test_worker_client_normalizes_the_server_url_like_client():
    """§3: `Client` 와 같은 정규화 — 스킴이 없으면 `http://`, 끝의 `/` 는 뗀다. 토큰은 그대로."""
    wc = client_mod.WorkerClient("127.0.0.1:1/", "tok")
    assert wc.server == "http://127.0.0.1:1"
    assert wc.token == "tok"
    assert client_mod.WorkerClient("http://build:8787/", "t").server == "http://build:8787"


# ── register ─────────────────────────────────────────────────────────────────


def test_register_returns_the_protocol_parameters(srv):
    """§3 · M5b-2 §2: `register(pool, lanes, host_name, version)` → 서버 응답 dict 그대로
    `{name, pool, lanes, heartbeat_seconds, worker_timeout_seconds, claim_wait_seconds}`. 서버 행의
    host_name 은 보낸 것."""
    wc = make_client(srv)
    body = wc.register(pool="linux", lanes=2, host_name="b02.local", version=__version__)
    assert body == {
        "name": WORKER,
        "pool": "linux",
        "lanes": 2,
        "heartbeat_seconds": HEARTBEAT,
        "worker_timeout_seconds": TIMEOUT,
        "claim_wait_seconds": CLAIM_WAIT,
    }
    row = srv.store.get_worker(WORKER)
    assert row is not None and (row.pool, row.lanes, row.host_name) == ("linux", 2, "b02.local")


def test_register_version_mismatch_is_409_with_the_servers_message(srv):
    """§3: 409 는 `ClientError(409, <서버 메시지>)` — `rcm worker` 가 이 메시지를 그대로 찍는다."""
    wc = make_client(srv)
    with pytest.raises(ClientError) as e:
        wc.register(pool="default", lanes=1, host_name="b02.local", version="0.0.1")
    assert e.value.status == 409
    assert e.value.message.startswith(f"worker version 0.0.1, server {__version__}")
    assert "install the same release" in e.value.message
    assert srv.store.get_worker(WORKER) is None


# ── claim ────────────────────────────────────────────────────────────────────


def test_claim_returns_none_on_204_and_the_payload_on_200(srv):
    """§3: 빈 풀 → 204 → `None`(`wait_seconds` 를 보내므로 서버 상한 20초를 기다리지 않는다). 잡이
    있으면 `{job, tree_url, preset}` payload 그대로 — `preset.argv` 가 들어 있다."""
    wc = make_client(srv)
    register(wc)
    t0 = time.monotonic()
    assert wc.claim(lane=1, wait_seconds=0) is None
    assert time.monotonic() - t0 < 5, "claim ignored wait_seconds=0"
    jid = srv.queued_job()
    payload = wc.claim(lane=1, wait_seconds=0)
    assert payload is not None and set(payload) == {"job", "tree_url", "preset"}, payload
    assert payload["job"]["id"] == jid and payload["job"]["state"] == RUNNING
    assert payload["tree_url"] == f"/worker/jobs/{jid}/tree"
    assert payload["preset"]["argv"] == list(srv.cfg.preset("ok").argv)
    assert srv.store.get_job(jid).worker_name == WORKER


def test_claim_sends_the_lane(srv):
    """§3: `lane` 은 본문으로 간다 — 레인 2 로 claim 하면 잡의 lane 이 2 다."""
    wc = make_client(srv)
    register(wc, lanes=2)
    jid = srv.queued_job()
    payload = claimed(srv, wc, jid, lane=2)
    assert payload["job"]["lane"] == 2
    assert srv.store.get_job(jid).lane == 2
    assert srv.worker_lane(WORKER, 2)["state"] == "busy"


def test_claim_errors_carry_the_servers_status_and_message(srv):
    """§3: 등록 전 claim 은 `ClientError(409, 'worker <name> is not registered')`, 레인 과할당은
    `ClientError(409, 'lane 1 already has job #N')` — 서버 문구 그대로."""
    wc = make_client(srv)
    with pytest.raises(ClientError) as e:
        wc.claim(lane=1, wait_seconds=0)
    assert (e.value.status, e.value.message) == (409, f"worker {WORKER} is not registered")
    register(wc)
    jid = srv.queued_job()
    claimed(srv, wc, jid)
    with pytest.raises(ClientError) as e:
        wc.claim(lane=1, wait_seconds=0)
    assert (e.value.status, e.value.message) == (409, f"lane 1 already has job #{jid}")


# ── download_tree ────────────────────────────────────────────────────────────


def test_download_tree_streams_the_tar_to_dest_and_returns_the_byte_count(srv, tmp_path):
    """§3: `download_tree(job_id, dest)` 는 `<dest>.part` 로 흘려 받아 끝에 `dest` 로 옮기고 받은
    바이트 수를 돌려준다. 이전에 죽으며 남은 `<dest>.part` 는 덮어쓴다(이어 붙이지 않는다) —
    끝나면 `.part` 는 없다."""
    wc = make_client(srv)
    register(wc)
    jid = srv.queued_job()
    claimed(srv, wc, jid)
    dest = tmp_path / "ws" / "tree.tar.gz"
    dest.parent.mkdir()
    part = dest.with_name(dest.name + ".part")
    part.write_bytes(b"stale garbage from an earlier crash")
    n = wc.download_tree(jid, dest)
    assert n == len(TAR)
    assert dest.read_bytes() == TAR
    assert not part.exists()


def test_download_tree_of_a_cache_job_gets_the_assembled_tar(srv, tmp_path):
    """§3 · M5b-2 §3: 캐시 잡은 서버가 manifest+blob 으로 조립한 tar 를 준다 — 멤버가 manifest 와
    같고 돌려준 바이트 수가 파일 크기와 같다(조립본의 Content-Length 검증)."""
    files = {"a.txt": b"aaa", "sub/deep/c.txt": b"cc"}
    jid = srv.cache_job(files)
    wc = make_client(srv)
    register(wc)
    claimed(srv, wc, jid)
    dest = tmp_path / "tree.tar.gz"
    n = wc.download_tree(jid, dest)
    assert n == dest.stat().st_size > 0
    assert set(tar_members(dest.read_bytes())) == set(files)


def test_download_tree_of_a_git_ref_job_is_404_and_leaves_no_file(srv, tmp_path):
    """§3: git_ref 잡은 서버가 404 `git_ref jobs are fetched by the worker` — `ClientError(404)`,
    `dest` 도 `.part` 도 생기지 않는다."""
    jid = srv.git_ref_job()
    wc = make_client(srv)
    register(wc)
    claimed(srv, wc, jid)
    dest = tmp_path / "tree.tar.gz"
    with pytest.raises(ClientError) as e:
        wc.download_tree(jid, dest)
    assert e.value.status == 404
    assert e.value.message == "git_ref jobs are fetched by the worker"
    assert not dest.exists() and not dest.with_name(dest.name + ".part").exists()


def test_download_tree_rejects_a_short_body(fake, tmp_path):
    """§3 「Content-Length 검증」: 서버가 `Content-Length: 100` 을 주고 40 바이트만 보내고 끊으면
    `ClientError` — status 0(서버의 거절이 아니라 전송 문제), 메시지에 받은 수·기대한 수. `dest` 는
    만들어지지 않고 `.part` 도 남지 않는다(다음 시도가 깨진 파일을 tar 로 풀지 않게)."""
    f = fake((200, {"Content-Type": "application/gzip"}, b"x" * 40, 100))
    wc = client_mod.WorkerClient(f"http://127.0.0.1:{f.port}", "tok")
    dest = tmp_path / "tree.tar.gz"
    with pytest.raises(ClientError) as e:
        wc.download_tree(7, dest)
    assert e.value.status == 0, e.value.message
    assert re.search(r"\b40\b", e.value.message) and re.search(r"\b100\b", e.value.message), (
        e.value.message
    )
    assert not dest.exists()
    assert not dest.with_name(dest.name + ".part").exists()
    assert f.hits == [("GET", "/worker/jobs/7/tree")]


# ── phase · log ──────────────────────────────────────────────────────────────


def test_phase_and_log_report_to_the_server(srv):
    """§3: `phase(job_id, phase)` 는 큐 행의 phase 를 바꾸고, `log(job_id, data)` 는 raw 바이트를
    octet-stream 으로 보내 서버가 append + 마커 파싱한다 — 요청 경계에서 잘린 마커도 서버가
    잇는다."""
    wc = make_client(srv)
    register(wc)
    jid = srv.queued_job()
    claimed(srv, wc, jid)
    wc.phase(jid, "executing")
    assert srv.row(jid)["progress"]["phase"] == "executing"
    wc.log(jid, b"::rcm::steps::2\n::rcm::step::bu")
    wc.log(jid, b"ild\nhello\n")
    assert srv.log_text(jid) == "::rcm::steps::2\n::rcm::step::build\nhello\n"
    assert [(m.kind, m.value) for m in srv.store.markers(jid)] == [
        ("steps", "2"),
        ("step", "build"),
    ]
    p = srv.row(jid)["progress"]
    assert p["steps_total"] == 2 and p["current_name"] == "build"


def test_phase_rejected_by_the_server_is_a_400_client_error(srv):
    """§3: 서버가 거부한 phase(400)는 `ClientError(400, <메시지>)` — 조용히 삼키지 않는다."""
    wc = make_client(srv)
    register(wc)
    jid = srv.queued_job()
    claimed(srv, wc, jid)
    with pytest.raises(ClientError) as e:
        wc.phase(jid, "cooking")
    assert e.value.status == 400 and "phase" in e.value.message


# ── finish ───────────────────────────────────────────────────────────────────


def test_finish_returns_the_state_and_a_second_finish_is_409(srv):
    """§3: `finish(job_id, outcome, exit_code)` → `{job_id, state}`; 요약은 마커에서. 두 번째
    finish 는 `ClientError(409, 'job #N is succeeded')` 이고 `body['state']` 에 그 상태가 있다
    (워커가 「이미 닫힘 → 정리」를 알아보는 근거)."""
    wc = make_client(srv)
    register(wc)
    jid = srv.queued_job()
    claimed(srv, wc, jid)
    wc.log(jid, b"::rcm::step::build\n::rcm::summary::green\n")
    assert wc.finish(jid, "succeeded", 0) == {"job_id": jid, "state": SUCCEEDED}
    v = srv.view(jid)
    assert v["state"] == SUCCEEDED and v["exit_code"] == 0 and v["summary"] == "green"
    with pytest.raises(ClientError) as e:
        wc.finish(jid, "succeeded", 0)
    assert e.value.status == 409
    assert e.value.message == f"job #{jid} is succeeded"
    assert e.value.body.get("state") == SUCCEEDED
    assert srv.worker_lane(WORKER, 1)["state"] == "idle"


def test_finish_with_a_summary_and_no_exit_code_keeps_the_summary(srv):
    """§3: 자재화 실패처럼 프로세스가 뜨기 전 끝나면 `finish(job_id, 'failed', None, summary=…)` —
    요약은 보낸 그대로, exit_code 는 null. `lost` 도 같은 모양(summary `worker stopped`)."""
    wc = make_client(srv)
    register(wc, lanes=2)
    a = srv.queued_job()
    b = srv.queued_job(token="bob")
    claimed(srv, wc, a, lane=1)
    claimed(srv, wc, b, lane=2)
    assert wc.finish(a, "failed", None, summary="repo 'app' is not configured on this worker") == {
        "job_id": a,
        "state": FAILED,
    }
    v = srv.view(a)
    assert v["exit_code"] is None
    assert v["summary"] == "repo 'app' is not configured on this worker"
    assert wc.finish(b, "lost", None, summary="worker stopped")["state"] == LOST
    assert srv.view(b)["summary"] == "worker stopped"


def test_late_reports_after_the_job_closed_are_409(srv):
    """§3(M5b-2 리뷰 — 늦은 보고는 무시): 닫힌 잡에 대한 phase·log 도 409 `job #N is <state>` —
    워커는 포기하고 워크스페이스를 정리한다."""
    wc = make_client(srv)
    register(wc)
    jid = srv.queued_job()
    claimed(srv, wc, jid)
    wc.finish(jid, "succeeded", 0)
    for call in (lambda: wc.phase(jid, "executing"), lambda: wc.log(jid, b"late\n")):
        with pytest.raises(ClientError) as e:
            call()
        assert e.value.status == 409, e.value.message
        assert e.value.message == f"job #{jid} is succeeded"
    assert not srv.app.log_path(jid).exists()  # 늦은 log 는 파일도 만들지 않는다


def test_reports_for_another_workers_job_are_403_and_unknown_jobs_404(srv):
    """§3: 남의 잡은 403 `not your job`, 없는 잡은 404 `no such job` — 상태·메시지 그대로."""
    wc = make_client(srv)
    register(wc)
    jid = srv.queued_job()
    claimed(srv, wc, jid)
    other = make_client(srv, "build-03")
    register(other)
    with pytest.raises(ClientError) as e:
        other.finish(jid, "succeeded", 0)
    assert (e.value.status, e.value.message) == (403, "not your job")
    with pytest.raises(ClientError) as e:
        wc.log(999, b"x\n")
    assert (e.value.status, e.value.message) == (404, "no such job")
    assert srv.store.get_job(jid).state == RUNNING


# ── heartbeat ────────────────────────────────────────────────────────────────


def test_heartbeat_returns_cancel_paused_timeout_and_always_sends_the_job_list(srv):
    """§3: `heartbeat(jobs, sample)` → `{cancel, paused, timeout_seconds}`. 취소된 잡은 `cancel` 에.
    `jobs` 는 **빈 목록도 보낸다**(생략이 아니다) — 서버가 「워커가 잊은 잡」을 lost 로 닫는 조정
    규칙이 그 위에 선다."""
    wc = make_client(srv)
    register(wc)
    jid = srv.queued_job()
    claimed(srv, wc, jid)
    assert wc.heartbeat([jid], None) == {"cancel": [], "paused": False, "timeout_seconds": TIMEOUT}
    assert srv.cancel(jid)[1]["state"] == CANCELLING
    assert wc.heartbeat([jid], None)["cancel"] == [jid]
    srv.clock.advance(3)
    assert wc.heartbeat([], None)["cancel"] == []
    j = srv.store.get_job(jid)
    assert j.state == LOST and j.summary == f"worker {WORKER} restarted without the job"
    assert srv.store.get_worker(WORKER).last_seen_at == srv.clock.now


def test_heartbeat_host_sample_reaches_the_pools_hosts(srv):
    """§3 · M5b-2 §6 호스트 표본: `sample` 은 그 풀의 `hosts[]` 항목이 된다(`name` = 워커,
    `source = "worker"`). `None` 이면 표본이 없다."""
    wc = make_client(srv)
    register(wc)
    assert wc.heartbeat([], None)["paused"] is False
    assert srv.pools()["default"]["hosts"] == []
    wc.heartbeat([], SAMPLE)
    (h,) = srv.pools()["default"]["hosts"]
    assert h["name"] == WORKER and h["source"] == "worker" and h["cores"] == 8


# ── 인증 · 재시도 없음 · 연결 실패 ───────────────────────────────────────────


def test_auth_errors_carry_the_servers_status_and_message(srv):
    """§3 · M5b-2 §1: 무효 토큰 401, client 토큰 403 `worker token required` — `ClientError.status`
    와 서버 문구 그대로(`body == {"error": …}`). `rcm worker` 가 403 을 보고 안내를 붙인다."""
    bad = client_mod.WorkerClient(url_of(srv), "garbage-token")
    with pytest.raises(ClientError) as e:
        bad.heartbeat([], None)
    assert e.value.status == 401 and "token" in e.value.message
    alice = make_client(srv, "alice")
    with pytest.raises(ClientError) as e:
        register(alice)
    assert (e.value.status, e.value.message) == (403, WORKER_TOKEN_REQUIRED)
    assert e.value.body == {"error": WORKER_TOKEN_REQUIRED}


def test_a_503_is_raised_immediately_without_retries(fake):
    """§3 「재시도 없음」: 503 은 즉시 `ClientError(503, <error>)` — 요청은 정확히 한 번, 기다림
    없음. 재시도(1·2·4초)는 워커 루프의 정책이지 클라이언트의 것이 아니다."""
    f = fake((503, {"Content-Type": "application/json"}, b'{"error": "queue is busy"}', None))
    wc = client_mod.WorkerClient(f"http://127.0.0.1:{f.port}", "tok")
    t0 = time.monotonic()
    with pytest.raises(ClientError) as e:
        wc.register(pool="default", lanes=1, host_name="b02.local", version=__version__)
    assert time.monotonic() - t0 < 2
    assert (e.value.status, e.value.message) == (503, "queue is busy")
    assert f.hits == [("POST", "/worker/register")]


def test_an_unreachable_server_is_status_0_with_cannot_reach():
    """§3: 연결 실패는 `ClientError(0, 'cannot reach <server>: …')` — 워커 루프가 status 0 으로
    「재시도할 것」을 가른다."""
    port = free_port()
    wc = client_mod.WorkerClient(f"http://127.0.0.1:{port}", "tok")
    with pytest.raises(ClientError) as e:
        wc.claim(lane=1, wait_seconds=0)
    assert e.value.status == 0
    assert e.value.message.startswith(f"cannot reach http://127.0.0.1:{port}")


def test_log_sends_octet_stream_with_the_exact_length_and_a_bearer_token(fake):
    """§3 · M5b-2 §6 로그: `log` 는 `POST /worker/jobs/{id}/log`, `Content-Type:
    application/octet-stream`, 정확한 `Content-Length`(chunked 아님 — 서버는 411/415),
    `Authorization: Bearer <token>`."""
    f = fake((200, {"Content-Type": "application/json"}, b'{"job_id": 5, "bytes": 3}', None))
    wc = client_mod.WorkerClient(f"http://127.0.0.1:{f.port}", "tok")
    wc.log(5, b"hi\n")
    assert f.hits == [("POST", "/worker/jobs/5/log")]
    (headers,) = f.requests
    assert headers["Content-Type"] == "application/octet-stream"
    assert headers["Content-Length"] == "3"
    assert "Transfer-Encoding" not in headers
    assert headers["Authorization"] == "Bearer tok"


def test_download_tree_creates_missing_parent_directories(srv, tmp_path):
    """§3: `dest` 의 부모가 없어도 만들어 준다 — 워커의 `<data_dir>/jobs/<id>/tree.tar.gz` 는 첫
    잡에서 처음 생긴다."""
    wc = make_client(srv)
    register(wc)
    jid = srv.queued_job()
    claimed(srv, wc, jid)
    dest = tmp_path / "jobs" / str(jid) / "tree.tar.gz"
    assert wc.download_tree(jid, dest) == len(TAR)
    assert dest.is_file() and dest.read_bytes() == TAR


# ── M5f PR 2a-0: 폴링 지터 ──────────────────────────────────────────────────
#
# `CLAIM_MIN_INTERVAL` 이 고정 1.0 초라, 빈 204 를 받은 레인들이 같은 1초 격자에 묶여 영영
# 안 흩어진다. 실측(§15-C4): 같은 격자 32레인이면 `max_concurrent_requests` 가 정확히 가득 차고
# 48레인에서 첫 `/api/status` 503 이 난다. M5f 는 보류 레인을 전부 슬롯 없는 폴러로 만들어
# 이 lockstep 을 정상 상태로 만든다.


def _worker(rand):
    from remote_ci_monitor.config import WorkerConfig
    from remote_ci_monitor.remote_worker import RemoteWorker

    return RemoteWorker(
        WorkerConfig(server="http://x", token="t", pool="default", lanes=1),
        client=object(),
        rand_fn=rand,
    )


def test_backoff_never_goes_below_the_base():
    """`uniform(0, base)` 로 하면 하한이 0 이 돼 뜨거운 루프가 된다."""
    from remote_ci_monitor.remote_worker import CLAIM_MIN_INTERVAL

    assert _worker(lambda: 0.0)._backoff(CLAIM_MIN_INTERVAL) == CLAIM_MIN_INTERVAL


def test_backoff_spreads_over_one_whole_base():
    from remote_ci_monitor.remote_worker import CLAIM_MIN_INTERVAL, RETRY_WAIT_SECONDS

    w = _worker(lambda: 0.999)
    assert CLAIM_MIN_INTERVAL <= w._backoff(CLAIM_MIN_INTERVAL) < 2 * CLAIM_MIN_INTERVAL
    assert RETRY_WAIT_SECONDS <= w._backoff(RETRY_WAIT_SECONDS) < 2 * RETRY_WAIT_SECONDS


def test_backoff_draws_a_new_number_every_call():
    """한 번 뽑아 두면 그 워커의 레인들이 다시 같은 격자에 묶인다."""
    draws = iter([0.0, 0.25, 0.5, 0.75])
    w = _worker(lambda: next(draws))
    assert [w._backoff(1.0) for _ in range(4)] == [1.0, 1.25, 1.5, 1.75]


def test_backoff_uses_the_real_random_by_default():
    from remote_ci_monitor.config import WorkerConfig
    from remote_ci_monitor.remote_worker import RemoteWorker

    w = RemoteWorker(WorkerConfig(server="http://x", token="t"), client=object())
    seen = {round(w._backoff(1.0), 6) for _ in range(50)}
    assert len(seen) > 1 and all(1.0 <= v < 2.0 for v in seen)
