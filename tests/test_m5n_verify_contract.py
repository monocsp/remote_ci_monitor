"""M5n 격리 검증(계약 대조)이 찾은 결함 — 명세와 구현이 어긋난 곳마다 하나씩.

보고서: docs/acceptance/reports/2026-11-16-m5n-verify-contract.md. 서버 시험은
`test_server_m5n.M5nServer`(in-process HTTP · 주입 시계 · 로컬 레인 스레드 없음)를 그대로 쓴다.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from remote_ci_monitor import remote_worker as rw
from remote_ci_monitor.config import WorkerConfig
from test_cli_m5n import fake, run  # noqa: F401 — `fake` 는 픽스처다
from test_server_m5n import PRESETS, PROBES, M5nServer, expected_hash

ANYWHERE = 'name = "anywhere"\nplacement = "any-capable"\n'

# ── 1. 웹의 「다시 점검」은 `@local` 을 퍼센트 인코딩해 보낸다 ─────────────────


@pytest.fixture
def srv(tmp_path):
    s = M5nServer(tmp_path)
    yield s
    s.close()


def test_recheck_accepts_a_percent_encoded_local_machine_id(srv):
    """web/app.js 는 `encodeURIComponent("@local")` = `%40local` 로 부른다. 서버가 경로를 풀지
    않으면 Mac mini(로컬) 의 다시 점검 버튼만 언제나 404 다 — 가짜 서버는 경로를 풀어
    이것을 가렸다."""
    srv.app.measure_capabilities()
    plain = srv.req("POST", "/machines/@local/recheck", token="admin", json_body={})
    encoded = srv.req("POST", "/machines/%40local/recheck", token="admin", json_body={})
    assert plain[0] == 200, plain
    assert encoded[0] == 200, encoded
    assert [j["preset"] for j in encoded[1]["jobs"]] == ["simcheck"]


def test_recheck_still_refuses_an_encoded_slash(srv):
    status, _ = srv.req("POST", "/machines/a%2Fb/recheck", token="admin", json_body={})
    assert status in (400, 404)


# ── 2. reload 가 any-capable 프리셋을 바꿔도 줄 선 잡은 갇히지 않는다 ─────────


def test_a_capable_job_queued_before_a_reload_to_pool_is_still_claimed(srv):
    """잡은 제출 때의 `placement` 를 지닌다. claim 의 `capable` 목록을 **지금 설정의** placement
    로 거르면, reload 로 `any-capable` 을 뗀 프리셋의 대기 잡은 어느 갈래에도 안 걸려 영원히 선다
    — 화면은 그 잡에 시각을 약속한다(fail-open)."""
    srv.reported("build-02")
    jid = srv.queued("anywhere")
    srv.write(PROBES + PRESETS.replace(ANYWHERE, 'name = "anywhere"\n'))
    status, body = srv.req("POST", "/reload", token="admin", json_body={})
    assert status == 200, body
    gen = srv.machines_doc()["generation"]
    assert srv.report("build-02", gen)[0] == 200
    assert srv.claimed("build-02") == jid


def test_a_capable_job_of_a_removed_preset_is_claimed_and_ends_preset_missing(srv):
    """reload 로 프리셋이 사라지면 그 `capable` 대기 잡은 영원히 기다리지 않는다 — pool 잡처럼
    집혀서 `preset_missing` 으로 끝난다(엣지 검증 E9 와 합친 결론). 처음 고친 판(「아무도 안
    집고 시각도 없다」)은 잡을 큐에 영원히 남겼다."""
    srv.generation("mbp", pool="default")  # 표시 풀(default)에 살아 있는 레인 하나
    jid = srv.queued("anywhere")
    srv.write(PROBES + PRESETS.replace(ANYWHERE, ANYWHERE.replace("anywhere", "gone")))
    status, body = srv.req("POST", "/reload", token="admin", json_body={})
    assert status == 200, body
    assert srv.claimed("mbp") == jid


# ── 3. 다시 오지 않을 점검을 「스스로 풀린다」로 부르지 않는다 ───────────────


def _cancelled_auto_check(srv: M5nServer) -> None:
    fp = expected_hash({"xc": "Xc 1"})
    check = srv.queued("simcheck", machine="build-02")
    status, _ = srv.cancel(check)
    assert status == 200
    srv.store.record_machine_check("build-02", "simcheck", fp, check, srv.clock(), auto=True)
    srv.app._mark_dirty()


def test_a_cancelled_automatic_check_does_not_leave_the_pool_job_check_pending(srv):
    """자동 점검은 (워커, 점검, 지문)마다 한 번(결정 95)이라 취소된 점검은 다시 안 온다. 그 잡의
    이유가 `check_pending`(「확인이 필요한 작업」에 안 올라가는 「스스로 풀린다」)이면 아무도 모르게
    영원히 기다린다 — 이 마일스톤이 막으려던 「열흘 동안 조용히」 그대로다."""
    srv.reported("build-02")
    qa = srv.queued("scenario")
    _cancelled_auto_check(srv)
    assert srv.machines()["build-02"]["presets"]["scenario"]["code"] == "check_needed"
    row = srv.row(qa)
    assert row["reason"] != "check_pending"
    # 실기 검증(F2)과 합친 결론: 사람이 다시 걸어야 풀리는 점검은 `check_failed` 다
    assert row["reason"] == "check_failed"


def test_check_pending_still_means_a_check_will_come(srv):
    """같은 상황이라도 스위퍼가 아직 안 걸었으면(한 번 남았으면) 스스로 풀린다 — check_pending."""
    srv.reported("build-02")
    qa = srv.queued("scenario")
    assert srv.row(qa)["reason"] == "check_pending"


def test_auto_checks_off_does_not_say_check_pending(tmp_path):
    s = M5nServer(tmp_path, extra="auto_checks = false")
    try:
        s.reported("build-02")
        qa = s.queued("scenario")
        assert s.row(qa)["reason"] == "check_failed"  # 실기 검증 F2 와 같은 결론
    finally:
        s.close()


# ── 4. 원격 워커의 다시 재기는 heartbeat 을 붙잡지 않는다 ─────────────────────


class SlowSpecClient:
    """`WorkerClient` 대역 — heartbeat 은 새 세대를 알리고, `/worker/spec` 은 문이 열릴 때까지
    걸린다(느린 `xcodebuild -version` · 첫 `flutter --version` 의 대역)."""

    server = "http://127.0.0.1:1"

    def __init__(self, generation: str = "1:0"):
        self.gate = threading.Event()
        self.generation = generation
        self.specs = 0
        self.reports: list[dict[str, Any]] = []
        self.registers = 0

    def register(self, **kw) -> dict[str, Any]:
        self.registers += 1
        return {"heartbeat_seconds": 5, "name": "mbp", "generation": self.generation}

    def heartbeat(self, jobs, host_sample=None) -> dict[str, Any]:
        return {"generation": self.generation}

    def spec(self) -> dict[str, Any]:
        self.specs += 1
        self.gate.wait(10)
        return {"generation": self.generation, "probes": [], "presets": []}

    def capabilities(self, body: dict[str, Any]) -> dict[str, Any]:
        self.reports.append(body)
        return {"ok": True}


def _worker(client: SlowSpecClient) -> rw.RemoteWorker:
    cfg = WorkerConfig(server=client.server, token="t", pool="mbp", lanes=1)
    return rw.RemoteWorker(cfg, client=client, log=lambda _m: None, environ={"PATH": "/usr/bin"})


def _wait(pred, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


def test_a_generation_change_measures_without_holding_the_heartbeat():
    """세대가 바뀐 heartbeat 이 프로브를 **heartbeat 스레드에서** 돌리면, 프로브 묶음이
    `worker_timeout_seconds`(기본 60초)를 넘는 순간 서버는 이 워커를 down 으로 보고 도는 잡을
    lost 로 만든다(프로브 64개 × 최대 60초가 설정상 허용된다). heartbeat 은 곧바로 돌아와야 한다."""
    client = SlowSpecClient("1:1")
    w = _worker(client)
    w.generation = "1:0"
    started = time.monotonic()
    try:
        assert w.heartbeat_once() is True
        assert time.monotonic() - started < 2.0, "heartbeat waited for the probes"
    finally:
        client.gate.set()
    assert _wait(lambda: client.reports), "the measure still happens, off the heartbeat thread"
    assert _wait(lambda: w.generation == "1:1")


def test_a_re_registration_measures_again_on_the_next_heartbeat():
    """다시 등록하면 서버는 그 워커의 보고를 지운다(§19-5). 워커가 등록 응답의 세대를 제 것으로
    삼으면 다음 heartbeat 의 세대가 같아서 `probe_interval_seconds`(기본 10분) 동안 다시 재지
    않고, 그동안 any-capable 잡을 못 받는다."""
    client = SlowSpecClient("1:0")
    client.gate.set()
    w = _worker(client)
    w.register()
    assert w.measure_and_report() is True
    assert len(client.reports) == 1
    w.register()  # 레인이 「not registered」 를 받고 다시 등록했다
    assert w.heartbeat_once() is True
    assert _wait(lambda: len(client.reports) == 2), "no report after re-registering"


# ── 5. 다시 점검이 못 건 점검을 「#None」 으로 찍지 않는다 ──────────────────────


def test_a_recheck_that_could_not_resolve_the_ref_says_so(srv):
    """ref 해석이 실패하면(저장소에 닿지 않는다) 잡이 없다. 응답은 그 이유를 싣는다."""
    srv.app.measure_capabilities()
    status, body = srv.req("POST", "/machines/@local/recheck", token="admin", json_body={})
    assert status == 200, body
    assert body["jobs"] == [{"preset": "simcheck", "job_id": None, "error_code": "ref_unresolved"}]


def test_rcm_recheck_does_not_print_a_missing_job_id_for_a_check_it_could_not_submit(
    fake,  # noqa: F811 — test_cli_m5n 의 픽스처
    capsys,
):
    fake.route(
        "POST",
        "/machines/mbp/recheck",
        {"jobs": [{"preset": "sim-check", "job_id": None, "error_code": "ref_unresolved"}]},
    )
    code, out, err = run(capsys, ["recheck", "mbp"])
    assert "None" not in out + err, (out, err)
    assert "sim-check" in out + err and "ref_unresolved" in out + err, (out, err)
    assert code == 1, (out, err)
