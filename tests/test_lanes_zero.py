"""M5t-a PR3 — 분배 전용 코디네이터 `lanes = 0`(결정 243 · `docs/m5t-workplan.md` §4.2 · §7 TL 표).

`lanes = 0` 인 서버는 잡을 받아 큐에 두고 원격 워커에 나눠 주기만 한다. `@local` 은 살아 있지
않고, 후보 함수(`machines_for`, 결정 254)가 어느 프리셋에도 `@local` 을 후보로 내지 않는다.
`/api/health` 의 `capacity` 는 「지금 갈 수 있는 머신이 하나라도 있는 프리셋 수 / 전체 프리셋
수」다.

서버는 `tests/test_server_m5n.py` 의 `M5nServer`(in-process HTTP · 주입 시계 · 로컬 레인 스레드
없음)를 `lanes = 0` 머리로 띄운다. 프리셋과 도우미는 `tests/test_local_only.py` 의 것을 쓴다 —
원격 워커 `build-02` 는 기본 풀, `mbp` 는 `mac2` 풀이다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import test_server_m5n
from remote_ci_monitor.config import ConfigError, load_server_config
from remote_ci_monitor.core.model import QUEUED
from test_local_only import (
    LOCAL,
    OTHER,
    PRESETS,
    REMOTE,
    block,
    job_count,
    placement,
    post,
    queued,
    record_checks,
    report,
    stored_job,
    write_config,
)
from test_server_m5n import HOST_B, PROBES, M5nServer, ok_probe

LANES_ONE = "lanes = 1\n"
LANES_ZERO = "lanes = 0\n"


def zero(tmp_path: Path, *, lanes: int = 0, presets: str = PRESETS, **kw: Any) -> M5nServer:
    """`[server] lanes` 만 바꾼 `M5nServer`. 머리는 모듈 상수라 띄우는 동안만 바꿔 둔다."""
    head = test_server_m5n.HEAD
    assert LANES_ONE in head
    test_server_m5n.HEAD = head.replace(LANES_ONE, f"lanes = {lanes}\n")
    try:
        s = M5nServer(tmp_path, presets=presets, **kw)
    finally:
        test_server_m5n.HEAD = head
    s.pools_of = {REMOTE: "default", OTHER: "mac2", "mac2-worker": "mac2"}
    return s


def report_all(s: M5nServer, worker: str, *, host_key: str = HOST_B) -> None:
    """등록 + 설정의 **모든** 프리셋에 「전부 있음」 보고(`test_local_only.report` 는 그 파일의
    프리셋 이름만 싣는다)."""
    gen = s.registered(worker, pool=s.pools_of[worker])["generation"]
    body = {
        "generation": gen,
        "host_key": host_key,
        "accept_any": True,
        "fingerprint": {"xc": ok_probe("Xc 1")},
        "presets": {
            p.name: {"missing_tools": [], "workspace_tools": [], "missing_passthrough": []}
            for p in s.app.config.presets
        },
    }
    status, resp = s.req("POST", "/worker/capabilities", token=worker, json_body=body)
    assert status == 200, resp


@pytest.fixture
def lz(tmp_path):
    s = zero(tmp_path)
    yield s
    s.close()


# ── TL1 설정 · TL10 mDNS ────────────────────────────────────────────────────


def test_tl1_lanes_zero_loads_and_the_server_starts(tmp_path):
    p = write_config(tmp_path, LANES_ZERO + block("gate"))
    try:
        cfg = load_server_config(p, environ={}, check_tools=False)
        err = ""
    except ConfigError as e:
        cfg, err = None, str(e)
    assert cfg is not None, err
    assert cfg.server.lanes == 0
    # 음수는 여전히 기동 실패다
    bad = write_config(tmp_path, "lanes = -1\n" + block("gate"))
    with pytest.raises(ConfigError) as e:
        load_server_config(bad, environ={}, check_tools=False)
    assert "lanes" in str(e.value)
    (tmp_path / "srv").mkdir()
    s = zero(tmp_path / "srv", workers=True)
    try:
        assert s.app.workers == []  # 로컬 레인 스레드가 없다
        status, health = s.req("GET", "/api/health")
        assert status == 200 and health["ok"] is True, health
        assert health["lanes"] == 0
    finally:
        s.close()


def test_tl10_the_mdns_txt_carries_lanes_zero(tmp_path, monkeypatch):
    from remote_ci_monitor import server as server_module

    seen: list[dict[str, str]] = []

    class FakeResponder:
        def __init__(self, *args: Any, txt_fn: Any, **kw: Any):
            seen.append(txt_fn())

        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass

    monkeypatch.setattr(server_module, "Responder", FakeResponder)
    s = zero(tmp_path, extra='advertise = true\nbind = "0.0.0.0"\n', workers=True)
    try:
        assert seen, "the server did not advertise"
        assert seen[0]["lanes"] == "0", seen
    finally:
        s.close()


# ── TL2 · TL4 · TL9 큐 (e2e) ────────────────────────────────────────────────


def test_tl2_without_workers_a_job_waits_without_an_eta_and_at_local_is_409(lz):
    jid = queued(lz, "gate")
    assert lz.store.get_job(jid).state == QUEUED  # 실패가 아니라 대기다
    row = lz.row(jid)
    assert row["reason"] == "worker_down", row
    assert row["estimate"]["wait_seconds"] is None, row
    assert row["estimate"]["finish_at"] is None, row
    before = job_count(lz)
    status, body = post(lz, "gate", token="admin", machine=LOCAL)
    assert status == 409, body
    assert "no local lanes" in body["error"], body
    assert job_count(lz) == before


def test_tl4_a_waiting_job_goes_to_the_worker_that_attaches(lz):
    jid = queued(lz, "gate")
    assert placement(lz, jid).workers == frozenset()  # 아무도 없다 — 로컬 레인도 후보가 아니다
    report(lz, REMOTE)
    assert placement(lz, jid).workers == frozenset({REMOTE})
    assert lz.claimed(REMOTE) == jid
    assert lz.store.get_job(jid).worker_name == REMOTE


def test_tl9_the_queue_survives_a_restart(tmp_path):
    s = zero(tmp_path)
    jobs = [queued(s, "gate"), queued(s, "gateany")]
    tokens, clock = s.tokens, s.clock
    s.close()
    s = zero(tmp_path, tokens=tokens, clock=clock)
    try:
        for jid in jobs:
            assert s.store.get_job(jid).state == QUEUED
            row = s.row(jid)
            assert row["estimate"]["wait_seconds"] is None, row
            assert None not in placement(s, jid).workers  # 재시작 뒤에도 로컬 레인은 후보가 아니다
    finally:
        s.close()


# ── TL5 · TL6 · TL7 · TL8 `@local` ──────────────────────────────────────────


def test_tl5_eligible_now_never_names_at_local(lz):
    lz.app.measure_capabilities()
    report(lz, REMOTE)
    for preset in ("gate", "gateany"):
        status, body = post(lz, preset)
        assert status == 201, body
        assert LOCAL not in body["placement"]["eligible_now"], (preset, body["placement"])
        assert REMOTE in body["placement"]["eligible_now"], (preset, body["placement"])


CHECKED = PRESETS + block("gatechk", 'checks = ["simcheck"]')


def test_tl6_automatic_checks_never_target_at_local(tmp_path, monkeypatch):
    s = zero(tmp_path, presets=CHECKED)
    try:
        s.app.measure_capabilities()
        report_all(s, REMOTE)
        calls = record_checks(s, monkeypatch)
        s.app.sweep_checks()
        assert ("simcheck", REMOTE) in calls, calls  # 스위퍼는 돈다 — 원격의 gatechk 가 원한다
        assert not [c for c in calls if c[1] == LOCAL], calls
    finally:
        s.close()


def test_tl7_at_local_is_not_alive(lz):
    local = lz.machines()[LOCAL]
    assert local["alive"] is False, local
    assert local["lanes"] == 0
    views = {v.id: v for v in lz.app.machine_views(lz.clock())}
    assert views[LOCAL].alive is False


def test_tl8_the_local_probes_do_not_run(lz, monkeypatch):
    from remote_ci_monitor import hostsample

    ran: list[Any] = []
    real = hostsample.run_probes

    def spy(*args: Any, **kw: Any):
        ran.append(args)
        return real(*args, **kw)

    monkeypatch.setattr(hostsample, "run_probes", spy)
    lz.app.measure_capabilities()
    assert ran == []
    assert lz.app.report_of(LOCAL) is None


# ── TL15 · TO4 · TO12 갈 곳 없는 잡 ─────────────────────────────────────────


def _mark_local_only(text: str, name: str) -> str:
    head = f'name = "{name}"\nargv = ["sh", "-c", "echo {name}"]\n'
    assert head in text
    return text.replace(head, head + "local_only = true\n")


@pytest.mark.parametrize("how", ["restart", "reload"])
def test_tl15_jobs_that_lost_their_place_wait_with_no_capable_machine(tmp_path, how):
    if how == "restart":
        s = zero(tmp_path, lanes=1)  # 레인이 있던 서버에 낸 잡
        jobs = [queued(s, "rel"), stored_job(s, "gate", target=LOCAL).id]
        tokens, clock = s.tokens, s.clock
        s.close()
        s = zero(tmp_path, tokens=tokens, clock=clock)  # lanes 를 0 으로 바꿔 다시 띄운다
    else:
        s = zero(tmp_path)
        jobs = [stored_job(s, "gate").id]
        assert s.row(jobs[0])["reason"] == "worker_down"  # 표식 전: 풀에 워커가 없을 뿐이다
        s.write(PROBES + _mark_local_only(PRESETS, "gate"))
        status, body = s.req("POST", "/reload", token="admin", json_body={})
        assert status == 200, body
        assert s.app.config.preset("gate").local_only is True
    try:
        for jid in jobs:
            row = s.row(jid)
            assert row["reason"] == "no_capable_machine", row
            assert row["estimate"]["wait_seconds"] is None, row
            assert row["estimate"]["finish_at"] is None, row
            assert s.store.get_job(jid).state == QUEUED  # 실패로 닫지 않는다
    finally:
        s.close()


@pytest.mark.parametrize("preset", ["rel", "relany"])
def test_to4_a_local_only_submission_to_a_server_without_local_lanes_is_409(lz, preset):
    report(lz, REMOTE)  # 기본 풀에 산 원격이 있어도 후보가 아니다
    before = job_count(lz)
    status, body = post(lz, preset)
    assert status == 409, body
    assert body["error_code"] == "no_capable_machine", body
    assert "runs only on this server's local lanes" in body["error"], body
    assert "no local lanes" in body["error"], body
    assert job_count(lz) == before  # 잡이 큐에 들지 않는다


def test_to12_a_local_only_check_has_nowhere_to_go_and_no_check_job_is_made(lz, monkeypatch):
    lz.app.measure_capabilities()
    report(lz, REMOTE)
    calls = record_checks(lz, monkeypatch)
    lz.app.sweep_checks()
    assert not [c for c in calls if c[0] == "localcheck"], calls
    jid = queued(lz, "qadef")  # 기본 풀, checks = ["localcheck"](local_only)
    row = lz.row(jid)
    assert row["reason"] == "no_capable_machine", row
    assert row["estimate"]["wait_seconds"] is None, row
    verdict = lz.machines()[REMOTE]["presets"]["qadef"]
    assert verdict["code"] == "check_local_only", verdict
