"""M5n 격리 검증(엣지) — 새 코드가 거짓말하거나 멈추거나 큐를 굶기는 곳.

보고서는 docs/acceptance/reports/2026-11-16-m5n-verify-edge.md. 여기 있는 것은 그 보고서의
결함마다 하나씩, **고치기 전에 빨갛던** 시험이다.

1. `/api/status` 와 claim 이 보존된 잡 수에 끌려간다(M5f 결정 49 의 함정 재발) — 점검 기록 읽기가
   `jobs` 를 통째로 훑고, 대기 잡마다 · 머신마다 다시 읽었다.
2. 호스트를 모르는 claimant(보고 없는 원격 워커)는 `concurrency_scope = "host"` 그룹을 지나친다.
3. 원격 워커가 하트비트 스레드에서 프로브를 돌려, 느린 프로브가 하트비트를 끊고 도는 잡을
   `lost` 로 만든다. 프로브는 차례로 돌아 시간이 합쳐지고, 시한을 넘긴 프로브의 자식이 남는다.
4. 원격 보고의 문자열(프로브 값 · 도구 이름 · host_key)에 제어문자가 그대로 실린다.
5. 설정 견본의 `flutter --version` 은 상대 시각(「4 months ago」)을 찍어 지문을 흔든다.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from remote_ci_monitor.core.model import QUEUED
from test_server_m5n import HOST_A, HOST_B, M5nServer, facts, ok_probe
from test_store_m5n import NOW, add, claimant, local, take

PY = sys.executable


@pytest.fixture
def store(tmp_path):
    from remote_ci_monitor.store import Store

    s = Store(tmp_path / "rcm.sqlite3")
    yield s
    s.close()


# ── 1. 보존된 잡 수에 끌려가지 않는다 ─────────────────────────────────────────


def _plans(st, call) -> list[str]:
    """메서드가 **실제로 낸** SELECT 문의 계획들(시험에 SQL 을 베끼지 않는다)."""
    seen: list[str] = []
    st._conn().set_trace_callback(seen.append)
    try:
        call()
    finally:
        st._conn().set_trace_callback(None)
    plans = []
    for sql in seen:
        if not sql.lstrip().upper().startswith(("SELECT", "WITH")):
            continue
        rows = st._conn().execute("EXPLAIN QUERY PLAN " + sql).fetchall()
        plans.append(" | ".join(r[3] for r in rows))
    return plans


@pytest.mark.parametrize("worker", ["mbp", "@local"])
def test_check_records_do_not_scan_every_retained_job(store, worker):
    for _ in range(3):
        add(store, "gate")
    plans = _plans(
        store,
        lambda: store.check_records(worker, ["sim-check", "lint-check"], fingerprint_hash="f1"),
    )
    assert plans, "check_records issued no SELECT"
    for plan in plans:
        assert "SCAN jobs" not in plan, plan


def test_check_records_keep_their_meaning_with_the_fingerprint_filter(store):
    """지문으로 거르면 다른 지문의 기록만 빠진다 — 판정(`check_state`)이 보던 것은 그대로다."""
    mine = add(store, "sim-check", target="mbp", fingerprint_hash="f1")
    add(store, "sim-check", target="mbp", fingerprint_hash="f0")
    add(store, "sim-check", target="other", fingerprint_hash="f1")
    local_job = add(store, "sim-check", target="@local", fingerprint_hash="f1")
    got = store.check_records("mbp", ["sim-check"], fingerprint_hash="f1")
    assert [r.job_id for r in got] == [mine]
    got = store.check_records("@local", ["sim-check"], fingerprint_hash="f1")
    assert [r.job_id for r in got] == [local_job]
    # 지문을 안 주면 옛 뜻 그대로(모든 지문)
    assert len(store.check_records("mbp", ["sim-check"])) == 2


def _count_selects(st, call) -> int:
    seen: list[str] = []
    st._conn().set_trace_callback(seen.append)
    try:
        call()
    finally:
        st._conn().set_trace_callback(None)
    # 산출물 행 읽기(잡마다 하나)는 M5n 전부터 있던 것이다 — 이 시험의 주제가 아니다
    return sum(
        1 for s in seen if s.lstrip().upper().startswith("SELECT") and "FROM job_artifacts" not in s
    )


def test_status_reads_check_records_per_machine_not_per_waiting_job(tmp_path):
    """대기 잡이 늘어도 `/api/status` 한 번의 질의 수는 그대로다(대기 잡 × 머신으로 늘던 것)."""
    srv = M5nServer(tmp_path)
    try:
        for w in ("build-02", "mac2-worker", "mbp"):
            srv.reported(w, pool="mac2")
        srv.app.measure_capabilities()
        for _ in range(3):
            srv.queued("anywhere")
            srv.queued("scenario")
        srv.app.status(None)  # 스냅샷 · 중앙값 캐시를 데운다
        few = _count_selects(srv.store, lambda: srv.app.status(None))
        for _ in range(12):
            srv.queued("anywhere")
            srv.queued("scenario")
        srv.app.status(None)
        many = _count_selects(srv.store, lambda: srv.app.status(None))
        assert many == few, (few, many)
    finally:
        srv.close()


# ── 2. 호스트를 모르면 host 범위는 막는 쪽으로 ─────────────────────────────────


def test_an_unknown_host_claimant_does_not_bypass_a_host_scope_group(store):
    """보고 없는 원격 워커(host_key None)가 같은 컴퓨터에서 도는 시뮬레이터 잡을 못 본 척하면
    안 된다."""
    busy = add(store, "sim", group="sim", scope="host")
    assert take(store, local()).id == busy
    waiting = add(store, "sim", pool="mac2", group="sim", scope="host")
    blind = claimant("mac2-worker", "mac2", None, None)
    assert take(store, blind) is None
    assert store.get_job(waiting).state == QUEUED


def test_a_job_on_an_unknown_host_blocks_a_host_scope_group(store):
    """거꾸로 — 호스트 없이 집힌 잡도 그 그룹을 쥐고 있다(어느 컴퓨터인지 모를 뿐)."""
    busy = add(store, "sim", pool="mac2", group="sim", scope="host")
    blind = claimant("mac2-worker", "mac2", None, None)
    assert take(store, blind).id == busy
    waiting = add(store, "sim", group="sim", scope="host")
    assert take(store, local()) is None
    assert store.get_job(waiting).state == QUEUED


def test_host_scope_still_lets_a_known_different_host_run(store):
    busy = add(store, "sim", group="sim", scope="host")
    assert take(store, local()).id == busy
    other = add(store, "sim", pool="mbp", group="sim", scope="host")
    job = take(store, claimant("mbp", "mbp", HOST_B, None))
    assert job is not None and job.id == other


def test_a_remote_claimant_without_a_report_keeps_its_remembered_host(tmp_path):
    """재등록은 보고를 지운다(결정 91) — 그래도 그 워커가 어느 컴퓨터인지는 `machines` 가 안다."""
    srv = M5nServer(tmp_path)
    try:
        srv.reported("mbp", pool="mbp", host_key=HOST_B)
        srv.registered("mbp", pool="mbp")  # 새 프로세스 — 보고가 지워진다
        assert srv.app.report_of("mbp") is None
        c = srv.app.claimant_for("mbp", "mbp", srv.clock())
        assert c.host_key == HOST_B
        assert c.capable == ()  # 그래도 any-capable 은 안 받는다(fail-closed 그대로)
    finally:
        srv.close()


def test_the_queue_estimate_treats_an_unknown_host_as_the_same_host():
    """ETA 도 claim 과 같은 규칙 — 모르는 호스트의 도는 잡은 host 범위 그룹을 쥐고 있다."""
    from remote_ci_monitor.core.queue import _GroupHold, _scope_matches

    hold = _GroupHold("sim", "mac2", None, NOW)
    assert _scope_matches("host", hold, "default", HOST_A)
    known = _GroupHold("sim", "mac2", HOST_B, NOW)
    assert _scope_matches("host", known, "default", None)
    assert not _scope_matches("host", known, "default", HOST_A)


# ── 3. 프로브는 하트비트를 붙잡지 않는다 ─────────────────────────────────────


def _probe(name: str, code: str, timeout: int = 10):
    from remote_ci_monitor.config import Probe

    return Probe(name=name, argv=(PY, "-c", code), timeout_seconds=timeout)


def test_probes_run_side_by_side(tmp_path):
    """64개 × 60초가 차례로면 서버 기동 · reload · 워커 측정이 한 시간을 넘는다. 함께 돌면 가장 긴
    것 하나만큼이다."""
    from remote_ci_monitor.hostsample import run_probes

    probes = [_probe(f"p{i}", "import time; time.sleep(1.0); print('v1')") for i in range(4)]
    began = time.monotonic()
    out = run_probes(probes, {"PATH": os.environ.get("PATH", "")})
    took = time.monotonic() - began
    assert {r.state for r in out.values()} == {"ok"}
    assert list(out) == [p.name for p in probes]  # 설정 순서 그대로
    assert took < 2.5, took


def test_a_probe_past_its_timeout_takes_its_children_with_it(tmp_path):
    from remote_ci_monitor.hostsample import run_probes

    pidfile = tmp_path / "child.pid"
    code = (
        "import subprocess, sys, time;"
        f"c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']);"
        f"open({str(pidfile)!r}, 'w').write(str(c.pid));"
        "time.sleep(60)"
    )
    out = run_probes([_probe("hang", code, timeout=1)], {"PATH": os.environ.get("PATH", "")})
    assert out["hang"].state == "timeout"
    pid = int(pidfile.read_text())
    deadline = time.monotonic() + 3
    alive = True
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            alive = False
            break
        time.sleep(0.05)
    if alive:
        os.kill(pid, 9)
    assert not alive, "the probe's child outlived the timeout"


def test_a_slow_measurement_does_not_hold_the_heartbeat(monkeypatch):
    """하트비트가 `worker_timeout_seconds` 를 넘게 끊기면 서버는 그 워커의 도는 잡을 lost 로 닫는다.
    세대가 바뀐 하트비트는 측정을 **걸기만** 하고 바로 돌아와야 한다."""
    from remote_ci_monitor import remote_worker as rw
    from remote_ci_monitor.config import WorkerConfig

    release = threading.Event()
    started = threading.Event()

    class Client:
        server = "http://10.0.0.2:8787"
        token = "t"

        def heartbeat(self, jobs, host_sample=None):
            return {"generation": "1:1"}

        def spec(self):
            return {"generation": "1:1", "probes": [], "presets": []}

        def capabilities(self, body):
            return {"ok": True}

    cfg = WorkerConfig(server="http://10.0.0.2:8787", token="t", pool="mbp", lanes=1)
    w = rw.RemoteWorker(cfg, client=Client(), log=lambda _m: None)
    w.generation = "1:0"

    def slow(spec: dict[str, Any]) -> dict[str, Any]:
        started.set()
        release.wait(10)
        return {"generation": spec["generation"]}

    monkeypatch.setattr(w, "measure", slow)
    began = time.monotonic()
    try:
        assert w.heartbeat_once() is True
        took = time.monotonic() - began
        assert started.wait(5), "the measurement never started"
        assert took < 1.0, took
    finally:
        release.set()
    deadline = time.monotonic() + 5
    while w.generation != "1:1" and time.monotonic() < deadline:
        time.sleep(0.02)
    assert w.generation == "1:1"


# ── 4. 원격 보고의 문자열 ───────────────────────────────────────────────────


def test_report_strings_lose_control_characters(tmp_path):
    srv = M5nServer(tmp_path)
    try:
        gen = srv.generation("mbp", pool="mbp")
        status, body = srv.report(
            "mbp",
            gen,
            host_key="ab\x1b[2Jcd\n",
            probes={"xc": ok_probe("Xc 1\x1b]0;pwn\x07\n[rcm] error: forged")},
            presets=facts(capgate={"missing_tools": ["tool\x1b[31m\nx"]}),
        )
        assert status == 200, body
        m = srv.machines()["mbp"]
        value = m["fingerprint"]["probes"]["xc"]["value"]
        assert not any(ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F for ch in value), value
        assert m["host_key"] == "worker:mbp", m["host_key"]
        tools = m["presets"]["capgate"]["detail"]["tools"]
        assert all(ch.isprintable() for t in tools for ch in t), tools
    finally:
        srv.close()


# ── 5. 견본 프로브가 지문을 흔들지 않는다 ─────────────────────────────────────


def test_the_sample_flutter_probe_prints_no_relative_time():
    """`flutter --version` 의 둘째 줄은 「revision 058e0af2c2 (4 months ago)」다 — 달이 바뀌면
    지문이 바뀌고 점검이 다시 돈다. `--machine` 은 같은 판이면 같은 줄을 낸다."""
    root = Path(__file__).resolve().parents[1]
    for path in (
        root / "src/remote_ci_monitor/templates/server.toml",
        root / "examples/server.toml",
    ):
        text = path.read_text(encoding="utf-8")
        lines = [ln for ln in text.splitlines() if "flutter" in ln and "argv" in ln]
        assert lines, path
        assert all("--machine" in ln for ln in lines), (path, lines)


def test_a_claimant_without_any_report_or_memory_has_no_host(tmp_path):
    """기억도 없으면 None 이다(지어내지 않는다) — 그리고 1·2 의 claim 규칙이 막는 쪽으로 받는다."""
    srv = M5nServer(tmp_path)
    try:
        srv.registered("mbp", pool="mbp")
        c = srv.app.claimant_for("mbp", "mbp", srv.clock() + timedelta(seconds=1))
        assert c.host_key is None
    finally:
        srv.close()
