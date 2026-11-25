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

import re
from pathlib import Path
from typing import Any

import pytest

import test_server_m5n
from remote_ci_monitor.config import ConfigError, load_server_config
from remote_ci_monitor.core.model import QUEUED, Requester, Source
from test_local_only import (
    FAKE_TOOL,
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
    tmp_path.mkdir(parents=True, exist_ok=True)
    head = test_server_m5n.HEAD
    assert LANES_ONE in head
    test_server_m5n.HEAD = head.replace(LANES_ONE, f"lanes = {lanes}\n")
    try:
        s = M5nServer(tmp_path, presets=presets, **kw)
    finally:
        test_server_m5n.HEAD = head
    s.pools_of = {REMOTE: "default", OTHER: "mac2", "mac2-worker": "mac2"}
    return s


def report_all(s: M5nServer, worker: str, missing: dict[str, list[str]] | None = None) -> None:
    """등록 + 설정의 **모든** 프리셋 보고(`test_local_only.report` 는 그 파일의 프리셋 이름만
    싣는다). `missing` 에 든 프리셋만 그 도구가 빠졌다고 보낸다."""
    missing = missing or {}
    gen = s.registered(worker, pool=s.pools_of[worker])["generation"]
    body = {
        "generation": gen,
        "host_key": HOST_B,
        "accept_any": True,
        "fingerprint": {"xc": ok_probe("Xc 1")},
        "presets": {
            p.name: {
                "missing_tools": missing.get(p.name, []),
                "workspace_tools": [],
                "missing_passthrough": [],
            }
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


def test_tl4_tl12_a_default_pool_job_runs_on_the_attached_worker_and_its_result_returns(lz):
    # TL12 는 실기(PR7)다 — 여기서는 단위 · e2e 로 닿는 데까지: 분배 전용 서버에 기본 풀 워커를
    # 붙이고, 배치를 바꾸지 않은 기본 풀 프리셋을 그 워커가 집어 끝내면 요청자가 결과를 읽는다
    jid = queued(lz, "gate")
    report(lz, REMOTE)
    assert lz.claimed(REMOTE) == jid
    status, body = lz.finish(REMOTE, jid, "succeeded", exit_code=0)
    assert status == 200, body
    view = lz.view(jid, token="alice")
    assert view["state"] == "succeeded", view
    assert view["worker"] == REMOTE, view


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
    # 비교 행: 같은 프리셋 · 같은 원격에서 레인이 하나 있으면 스위퍼가 `@local` 도 겨냥한다 —
    # 위의 「없음」이 스위퍼가 `@local` 을 원래 안 보는 탓이 아니라 lanes = 0 의 결과다
    s = zero(tmp_path / "lanes1", lanes=1, presets=CHECKED)
    try:
        s.app.measure_capabilities()
        report_all(s, REMOTE)
        calls = record_checks(s, monkeypatch)
        s.app.sweep_checks()
        assert ("simcheck", LOCAL) in calls, calls
        assert ("simcheck", REMOTE) in calls, calls
    finally:
        s.close()


def test_tl7_at_local_is_not_alive(lz):
    local = lz.machines()[LOCAL]
    assert local["alive"] is False, local
    assert local["lanes"] == 0
    views = {v.id: v for v in lz.app.machine_views(lz.clock())}
    assert views[LOCAL].alive is False


def test_tl7_recheck_on_at_local_says_this_server_has_no_local_lanes(tmp_path):
    """`@local` 다시 점검은 제출과 같은 409 다 — 「start rcm worker there」는 엉뚱한 조언이다
    (`@local` 은 워커를 띄워 살릴 머신이 아니다). 레인이 있는 서버에서 꺼진 원격 워커는 오늘처럼
    「start rcm worker there」(비교 행)."""
    s = zero(tmp_path, presets=CHECKED)
    try:
        status, body = s.req("POST", f"/machines/{LOCAL}/recheck", token="admin", json_body={})
        assert status == 409, body
        assert "no local lanes (lanes = 0)" in body["error"], body
        assert "start rcm worker" not in body["error"], body
        assert body.get("error_code") == "no_capable_machine", body
        report_all(s, REMOTE)
        s.clock.advance(s.app.config.server.worker_timeout_seconds + 5)
        status, body = s.req("POST", f"/machines/{REMOTE}/recheck", token="admin", json_body={})
        assert status == 409 and "start rcm worker there" in body["error"], body
    finally:
        s.close()


def test_tl7_rcm_check_does_not_count_at_local_as_a_machine(lz, home, monkeypatch, capsys):
    """`machines` 행의 「N machines」는 잡을 받을 수 있는 머신이다 — 분배 전용 서버의 `@local`
    은 세지 않는다. 원격 워커가 붙으면 그 하나만 센다."""
    code, found = check(lz, monkeypatch, capsys)
    assert found["machines"][1].startswith("0 machines"), found["machines"]
    report(lz, REMOTE)
    code, found = check(lz, monkeypatch, capsys)
    assert found["machines"][1].startswith("1 machine"), found["machines"]
    assert not found["machines"][1].startswith("1 machines"), found["machines"]


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


#: alice 가 낸 tree 잡의 출처 — 같은 레포 · 같은 브랜치(supersede 규칙 2 · 3)
def _tree(tree_hash: str) -> Source:
    return Source(mode="tree", repo="org/app", branch="main", tree_hash=tree_hash)


def _waiting(s: M5nServer, *, target: str | None, tree_hash: str) -> int:
    """제출 검사를 거치지 않은 alice 의 대기 `gate` 잡(재시작 전부터 있던 잡)."""
    return s.store.create_job(
        preset="gate",
        inputs={},
        key="gate",
        concurrency_group=None,
        source=_tree(tree_hash),
        requester=Requester(name="alice-laptop", label="alice@host"),
        timeout_seconds=600,
        join_key=None,
        now=s.clock(),
        state=QUEUED,
        pool="default",
        placement="pool",
        target_worker=target,
    ).id


def test_tl15_a_job_with_nowhere_to_go_says_no_local_lanes(lz):
    """# TL15 · P5 G5 — 분배 전용 서버에서 갈 곳 잃은 `@local` 지정 잡 · `local_only` 잡의 이유는
    「no capable machine · unknown」이 아니라 왜 없는지(로컬 레인 없음)를 머신 메모로 싣는다.
    터미널(`rcm status`)도 그 메모를 읽는다."""
    from remote_ci_monitor.core.render_text import placement_reason_text

    jobs = [stored_job(lz, "gate", target=LOCAL).id, stored_job(lz, "relany").id]
    for jid in jobs:
        row = lz.row(jid)
        assert row["reason"] == "no_capable_machine", row
        notes = row["placement_detail"]
        assert [n["code"] for n in notes] == ["no_local_lanes"], notes
        assert notes[0]["worker"] == LOCAL, notes
        text = placement_reason_text(row) or ""
        assert "unknown" not in text and "no local lanes" in text, text


def _lost_at_local(tmp_path: Path, presets: str = PRESETS) -> tuple[M5nServer, int]:
    """레인 1 서버에 `@local` 을 지정해 둔 대기 잡 → lanes 0 으로 다시 띄운 서버와 그 잡."""
    s = zero(tmp_path, lanes=1, presets=presets)
    jid = _waiting(s, target=LOCAL, tree_hash="pinned-tree")
    tokens, clock = s.tokens, s.clock
    s.close()
    s = zero(tmp_path, presets=presets, tokens=tokens, clock=clock)
    assert s.row(jid)["reason"] == "no_capable_machine"
    return s, jid


def test_tl15_a_job_that_lost_its_place_can_be_cancelled(tmp_path):
    """# TL15 · P5 G2 — 갈 곳을 잃은 `@local` 잡은 실패로 닫히지 않고 기다린다. 사람이 취소하면
    도는 잡이 없으니 곧장 cancelled 다(cancelling 에 남지 않는다)."""
    s, jid = _lost_at_local(tmp_path)
    try:
        status, body = s.req("POST", f"/jobs/{jid}/cancel", token="admin", json_body={})
        assert status == 200, body
        assert s.store.get_job(jid).state == "cancelled", s.store.get_job(jid)
        assert all(r["id"] != jid for p in s.status()["pools"] for r in p["queue"])
    finally:
        s.close()


#: 반복 정책이 있는 기본 풀 git_ref 프리셋(`tests/test_local_only.py` 의 RETRY 와 같은 꼴)
RETRYING = block(
    "relgitretry",
    'source_modes = ["git_ref"]\nrepo = "app"',
    "[presets.retry]\non_exit = 75\ninterval_seconds = 60\nmax_attempts = 3",
)


def test_tl15_the_retry_of_a_job_pinned_to_at_local_waits_with_no_capable_machine(tmp_path):
    """# TL15 · P5 G3 — 반복의 다음 시도는 제출을 거치지 않고(409 는 제출 경로뿐) 지정 머신을
    물려받는다. lanes 를 0 으로 바꿔 다시 띄우면 그 시도도 TL15 와 같은 규칙이다: 실패로 닫지
    않고 `no_capable_machine` · ETA null 로 기다린다."""
    s = zero(tmp_path, lanes=1, presets=PRESETS + RETRYING)
    jid = queued(s, "relgitretry", token="admin", machine=LOCAL)
    now = s.clock()
    first = s.store.claim(1, now, claimant=s.app.local_claimant(now))
    assert first is not None and first.id == jid
    assert s.store.finish(jid, "retried", now=s.clock(), exit_code=75)
    nxt = int(s.store.get_job(jid).retried_as)
    assert s.store.get_job(nxt).target_worker == LOCAL
    tokens, clock = s.tokens, s.clock
    s.close()
    s = zero(tmp_path, presets=PRESETS + RETRYING, tokens=tokens, clock=clock)
    try:
        s.clock.advance(61)
        s.app.tick_schedule()
        assert s.store.get_job(nxt).state == QUEUED  # 실패로 닫지 않는다
        row = s.row(nxt)
        assert row["reason"] == "no_capable_machine", row
        assert row["estimate"]["wait_seconds"] is None, row
        assert row["estimate"]["finish_at"] is None, row
        report(s, REMOTE)  # 원격이 붙어도 `@local` 지정 잡은 가져가지 않는다
        assert s.claim(REMOTE)[0] == 204
    finally:
        s.close()


#: tree 출처 · 같은 브랜치의 새 제출이 옛 잡을 치우는 프리셋
SUPERSEDING = PRESETS.replace(
    'name = "gate"\nargv = ["sh", "-c", "echo gate"]\n',
    'name = "gate"\nargv = ["sh", "-c", "echo gate"]\nsupersede = "all"\n',
)


def test_tl15_supersede_leaves_a_job_pinned_to_at_local_alone(tmp_path):
    """# TL15 · P5 G3 — 갈 곳을 잃은 `@local` 지정 잡은 같은 브랜치의 새 제출이 치우지 않는다
    (supersede 규칙 8: 사람이 고른 머신). 새 잡은 지정 머신이 없어 기본 풀에서 기다린다.
    비교 행: 같은 꼴인데 지정 머신이 없는 옛 잡은 치운다 — supersede 는 돈다."""
    assert SUPERSEDING != PRESETS
    s, pinned = _lost_at_local(tmp_path, presets=SUPERSEDING)
    try:
        loose = _waiting(s, target=None, tree_hash="loose-tree")
        fresh = queued(s, "gate")
        assert s.store.get_job(loose).state == "cancelled"  # 지정 없는 옛 잡은 치운다
        assert s.store.get_job(pinned).state == QUEUED  # 지정 잡은 그대로 기다린다
        assert s.row(pinned)["reason"] == "no_capable_machine"
        assert s.store.get_job(fresh).target_worker is None
        assert s.row(fresh)["reason"] == "worker_down", s.row(fresh)  # 워커를 기다린다
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


# ── TL3 · TL13 · TL17 · TO15 `/api/health` 의 `capacity` ─────────────────────


def health(s: M5nServer) -> tuple[int, dict[str, Any]]:
    status, body = s.req("GET", "/api/health")
    return status, body


def capacity(s: M5nServer) -> dict[str, Any]:
    body = health(s)[1]
    assert "capacity" in body, sorted(body)
    return body["capacity"]


def test_tl3_health_capacity_is_zero_without_workers_and_the_status_stays_200(lz):
    status, body = health(lz)
    assert status == 200 and body["ok"] is True, body  # HTTP 상태는 그대로다(⛔ Q5)
    names = [p.name for p in lz.app.config.presets]
    assert capacity(lz) == {"total": len(names), "reachable": 0, "unreachable": names}


def test_tl13_pools_without_workers_counts_the_default_pool_only_without_local_lanes(tmp_path):
    for lanes, expected in ((0, ["default"]), (1, [])):
        s = zero(tmp_path / f"lanes{lanes}", lanes=lanes)
        try:
            report(s, REMOTE)  # 기본 풀의 원격 워커 하나
            assert health(s)[1]["pools_without_workers"] == []  # 살아 있는 동안은 없다
            s.clock.advance(s.app.config.server.worker_timeout_seconds + 5)
            assert health(s)[1]["pools_without_workers"] == expected, lanes
        finally:
            s.close()


#: 도구가 빠진 원격 판정 — pool 프리셋은 판정을 보지 않고, any-capable 은 본다
GATED = PRESETS + block("gatechk", 'checks = ["simcheck"]')


def test_tl17_capacity_counts_live_candidates_and_pool_presets_ignore_the_verdict(tmp_path):
    s = zero(tmp_path, presets=GATED)
    try:
        report_all(s, REMOTE, {"gate": ["nope"], "gateany": ["nope"]})
        cap = capacity(s)
        # gate(pool · checks 없음)는 도구가 빠져도 그 풀의 살아 있는 레인을 센다
        assert "gate" not in cap["unreachable"], cap
        # gateany(any-capable)는 판정을 본다 — 도구가 빠진 머신은 갈 곳이 아니다
        assert "gateany" in cap["unreachable"], cap
        # 다른 풀 전용(qa · mac2)은 기본 풀 워커로 셀 수 없다
        assert "qa" in cap["unreachable"], cap
        assert cap["reachable"] == cap["total"] - len(cap["unreachable"])
        # 죽은 워커는 세지 않는다
        s.clock.advance(s.app.config.server.worker_timeout_seconds + 5)
        cap = capacity(s)
        assert cap["reachable"] == 0 and "gate" in cap["unreachable"], cap
    finally:
        s.close()
    # 프리셋이 0개면 0/0 이다(`rcm check` 는 행을 그리지 않는다, TL17 의 나머지는 cli 시험)
    s = zero(tmp_path / "none", presets="")
    try:
        assert capacity(s) == {"total": 0, "reachable": 0, "unreachable": []}
    finally:
        s.close()


def test_to15_capacity_never_counts_a_remote_machine_for_a_local_only_preset(tmp_path):
    # TO15 의 `capacity` 진입점(PR3). 레인이 있는 서버에서 `@local` 은 아직 재지 않아(보고 없음)
    # any-capable 을 받지 못한다. 그러면 relany 가 갈 곳은 원격 `build-02` 뿐인데, 후보 함수가
    # 원격을 빼므로 세지 않는다. 표식 없는 gateany 는 같은 원격으로 센다(비교 행). `lanes = 0`
    # 이면 로컬 레인이 없다는 이유가 먼저 후보를 비워 원격 제외를 가리므로 여기서는 레인 1 이다
    s = zero(tmp_path, lanes=1)
    try:
        report(s, REMOTE)
        assert s.app.report_of(LOCAL) is None
        cap = capacity(s)
        assert "gateany" not in cap["unreachable"], cap
        assert "relany" in cap["unreachable"], cap
        assert "relgated" in cap["unreachable"], cap
    finally:
        s.close()
    # 「pool 프리셋은 판정을 보지 않는다」는 후보 함수 결과 안에서만이다(6-A-L2 · 6-B-V14) —
    # 분배 전용 서버에서 표식 없는 gate 는 원격으로 세고 같은 풀 · 같은 판정인 rel 은 세지 않는다
    s = zero(tmp_path / "zero")
    try:
        report(s, REMOTE)
        cap = capacity(s)
        assert "gate" not in cap["unreachable"], cap
        for name in ("rel", "relany", "relq", "relgated", "localcheck"):
            assert name in cap["unreachable"], (name, cap)
    finally:
        s.close()


# ── TL3 · TL14 · TL16 · TL17 `rcm check` ────────────────────────────────────

#: 기본 풀(pool 배치)인데 도구가 어디에도 없는 프리셋 — 레인이 있는 서버의 도구 행이 FAIL 이 된다
POOLNEED = PRESETS + block("poolneed", f'requires = ["{FAKE_TOOL}"]')


@pytest.fixture
def home(monkeypatch, tmp_path) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("HOME", str(h))
    for var in ("XDG_CONFIG_HOME", "RCM_CONFIG", "RCM_SERVER", "RCM_TOKEN", "RCM_LABEL"):
        monkeypatch.delenv(var, raising=False)
    return h


#: `cmd_check` 의 행 — `{등급}  {이름:<13} {상세}`. 이름이 13자를 넘으면(`local preset tools`)
#: 칸을 넘친다. 그래서 행은 알려진 이름으로 찾는다(`ROW_NAMES`)
ROW_RE = re.compile(r"^(ok |warn|FAIL)  (.*)$")
ROW_NAMES = (
    "capacity",
    "machines",
    "presets",
    "pools",
    "server",
    "local preset tools",
    "service preset tools",
)


def check(s: M5nServer, monkeypatch, capsys, *, config: bool = False) -> tuple[int, dict[str, Any]]:
    """`rcm check` — (종료 코드, {행 이름: (등급, 상세)}). `config` 면 그 서버의 설정 파일도
    로컬 설정으로 읽힌다(`local preset tools` 행)."""
    from remote_ci_monitor.cli import main

    monkeypatch.setenv("RCM_SERVER", f"http://127.0.0.1:{s.port}")
    monkeypatch.setenv("RCM_TOKEN", s.tokens["alice"])
    argv = ["check", "--config", str(s.config_path)] if config else ["check"]
    code = main(argv)
    out = capsys.readouterr().out
    found: dict[str, tuple[str, str]] = {}
    for line in out.splitlines():
        m = ROW_RE.match(line)
        assert m, line
        grade, rest = m.group(1).strip(), m.group(2)
        for name in ROW_NAMES:
            if rest.startswith(name + " "):
                found[name] = (grade, rest[len(name) :].strip())
    return code, found


def test_tl3_rcm_check_fails_the_capacity_row_when_no_preset_has_a_machine(
    lz, home, monkeypatch, capsys
):
    code, found = check(lz, monkeypatch, capsys)
    assert "capacity" in found, found
    grade, detail = found["capacity"]
    total = len(lz.app.config.presets)
    assert grade == "FAIL", found["capacity"]
    assert detail.startswith(f"0/{total} presets"), detail
    assert "gate" in detail and "rel" in detail, detail
    assert code == 1


def test_tl14_rcm_check_lists_the_presets_no_machine_can_take_as_a_warning(
    lz, home, monkeypatch, capsys
):
    report(lz, REMOTE)
    code, found = check(lz, monkeypatch, capsys)
    assert "capacity" in found, found
    grade, detail = found["capacity"]
    assert grade == "warn", found["capacity"]  # 일부만 0 이면 FAIL 이 아니다
    for name in ("rel", "relany", "qa"):  # local_only 둘과 다른 풀 전용 하나
        assert name in detail, detail
    assert "gate," not in detail and not detail.endswith("gate"), detail


def _capacity_breaks(s: M5nServer, monkeypatch) -> None:
    """서버가 `capacity` 를 못 센다 — `/api/health` 는 키를 싣되 null 이다(모름)."""

    def broken() -> dict[str, Any]:
        raise RuntimeError("test: cannot count")

    monkeypatch.setattr(s.app, "capacity", broken)


@pytest.mark.parametrize("lanes", [0, 1])
def test_tl3_rcm_check_draws_capacity_unknown_when_the_server_could_not_count(
    tmp_path, home, monkeypatch, capsys, lanes
):
    """못 모은 칸은 실패로 그린다 — 빼거나 0 으로 하지 않는다(PLAN.md 「rcm check」). 분배 전용
    서버는 capacity 가 유일한 「갈 곳이 있나」 신호라 FAIL, 레인이 있는 서버는 warn 이다."""
    s = zero(tmp_path / f"lanes{lanes}", lanes=lanes)
    try:
        _capacity_breaks(s, monkeypatch)
        assert health(s)[1]["capacity"] is None
        code, found = check(s, monkeypatch, capsys)
        assert "capacity" in found, found
        grade, detail = found["capacity"]
        assert detail == "server could not count capacity", detail
        assert grade == ("FAIL" if lanes == 0 else "warn"), found["capacity"]
        if lanes == 0:
            assert code == 1
        # 서버 로그에 한 줄 남긴다 — 왜 못 셌는지는 로그에서 찾는다
        assert "capacity" in (s.app.last_error or ""), s.app.last_error
    finally:
        s.close()


def test_tl3_an_old_server_without_the_capacity_key_draws_no_row():
    from remote_ci_monitor.cli import _capacity_row

    # 키가 아예 없으면 옛 서버다 — 행이 없다. 키가 null 이면 서버가 못 센 것이다
    assert _capacity_row({"lanes": 0}, has_presets=True) is None
    assert _capacity_row({"lanes": 0, "capacity": None}, has_presets=False) is None
    row = _capacity_row({"lanes": 0, "capacity": None}, has_presets=True)
    assert row == ("capacity", False, "server could not count capacity"), row


def test_tl17_rcm_check_draws_no_capacity_row_without_presets(tmp_path, home, monkeypatch, capsys):
    s = zero(tmp_path / "none", presets="")
    try:
        code, found = check(s, monkeypatch, capsys)
        assert "presets" in found and "capacity" not in found, found
    finally:
        s.close()


def test_tl16_preset_tool_rows_say_this_machine_runs_no_jobs(tmp_path, home, monkeypatch, capsys):
    s = zero(tmp_path / "srv", presets=POOLNEED)
    try:
        s.app.measure_preset_env()
        code, found = check(s, monkeypatch, capsys, config=True)
        for name in ("local preset tools", "service preset tools"):
            row = found.get(name)
            assert row is not None, (name, found)
            assert row[0] != "FAIL", (name, row)
            assert "runs no jobs" in row[1], (name, row)
    finally:
        s.close()


def test_tl11_rcm_check_pools_row_names_the_default_pool_once(lz, home, monkeypatch, capsys):
    code, found = check(lz, monkeypatch, capsys)
    assert found["pools"][1] == "default (no local lanes, no workers)", found["pools"]
    report(lz, REMOTE)
    code, found = check(lz, monkeypatch, capsys)
    assert found["pools"][1] == "default (build-02/1 idle)", found["pools"]


# ── TD20 · TD38 · TL14 `rcm doctor --role coordinator` ──────────────────────


@pytest.fixture
def doctor_env(tmp_path, monkeypatch) -> Path:
    """`tests/test_doctor_cli.py` 의 `env` 와 같은 자리 — HOME 을 tmp 로, 셸의 RCM_* 은 없이."""
    from remote_ci_monitor import doctor

    home = tmp_path / "dhome"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("XDG_CONFIG_HOME", "RCM_CONFIG", "RCM_SERVER", "RCM_TOKEN", "RCM_WORKER_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(doctor, "is_tty", lambda: False)
    return home


def doctor_json(s: M5nServer, monkeypatch, capsys) -> tuple[int, dict[str, Any]]:
    """진짜 서버(`M5nServer`)에 `rcm doctor --role coordinator --json`. 서버의 답을 만드는 쪽과
    읽는 쪽을 그대로 잇는다 — 키 모양만 흉내 낸 가짜 서버로는 안 보이는 어긋남이 있다."""
    import json

    from remote_ci_monitor.cli import main

    monkeypatch.setenv("RCM_TOKEN", s.tokens["alice"])
    code = main(
        [
            "doctor",
            "--role",
            "coordinator",
            "--config",
            str(s.config_path),
            "--server",
            f"http://127.0.0.1:{s.port}",
            "--json",
        ]
    )
    doc = json.loads(capsys.readouterr().out)
    assert doc["exit"] == code, doc
    return code, doc


def test_td38_a_coordinator_without_lanes_exits_1_when_no_preset_has_a_machine(
    lz, doctor_env, monkeypatch, capsys
):
    code, doc = doctor_json(lz, monkeypatch, capsys)
    capacity = [p for p in doc["problems"] if "capacity 0/" in p]
    assert capacity, doc["problems"]
    assert "gate" in capacity[0], capacity
    assert code == 1, doc


def test_td38_tl14_a_coordinator_with_some_presets_unplaced_warns_and_lists_them(
    lz, doctor_env, monkeypatch, capsys
):
    report(lz, REMOTE)
    code, doc = doctor_json(lz, monkeypatch, capsys)
    assert not [p for p in doc["problems"] if "capacity" in p], doc["problems"]
    lines = [w for w in doc["warnings"] if w.startswith("capacity ")]
    assert len(lines) == 1, doc["warnings"]
    for name in ("rel", "relany", "qa"):
        assert name in lines[0], lines
    assert code != 1, doc  # 일부만 0 이면 1 이 아니다(판정 못 한 것이 있으면 3 일 수 있다)


def test_td20_the_coordinator_names_pool_only_presets_a_capable_worker_could_take(
    tmp_path, doctor_env, monkeypatch, capsys
):
    """`macbuild` 는 mac2 풀 전용인데 mac2 에는 워커가 없다. 기본 풀의 `build-02` 는 macbuild 를
    eligible 로 판정받았다 — any-capable 이면 받을 수 있다. doctor 는 그것을 알리고 설정은
    바꾸지 않는다."""
    s = zero(tmp_path, presets=PRESETS + block("macbuild", 'pool = "mac2"'))
    try:
        report_all(s, REMOTE)
        before = s.config_path.read_bytes()
        code, doc = doctor_json(s, monkeypatch, capsys)
        hints = [w for w in doc["warnings"] if w.startswith("macbuild:") and "any-capable" in w]
        assert hints and REMOTE in hints[0], doc["warnings"]
        # 어느 머신도 못 돌리는 프리셋은 capacity 줄에 든다
        assert any(w.startswith("capacity ") and "macbuild" in w for w in doc["warnings"])
        # local_only 프리셋은 배치를 바꿔도 원격에 가지 않는다 — 그 안내를 하지 않는다
        assert not [w for w in doc["warnings"] if w.startswith("rel:") and "any-capable" in w]
        assert s.config_path.read_bytes() == before  # 프리셋 설정을 스스로 바꾸지 않는다
    finally:
        s.close()


def test_td38_without_a_capacity_answer_a_lanes_zero_coordinator_is_undecided(
    lz, doctor_env, monkeypatch, capsys
):
    """서버가 capacity 를 못 셌거나(null) 옛 서버면 분배 전용 코디네이터의 판정은 모름(3)이다 —
    「문제 없음」(0)으로 적지 않는다."""

    def broken() -> dict[str, Any]:
        raise RuntimeError("test: cannot count")

    monkeypatch.setattr(lz.app, "capacity", broken)
    status, body = health(lz)
    assert status == 200 and body["capacity"] is None, body
    code, doc = doctor_json(lz, monkeypatch, capsys)
    assert any("does not report capacity" in u for u in doc["undecided"]), doc["undecided"]
    assert code == 3, doc


# ── TL11 터미널 표기 ────────────────────────────────────────────────────────


def test_tl11_rcm_top_says_no_local_lanes_and_marks_the_servers_host(lz):
    """`lanes 0/0 busy` 대신 「로컬 레인 없음」, 서버 자신의 호스트 줄에 「잡을 돌리지 않는
    머신」. 표본은 서버가 실제로 만드는 꼴(`source: "local"`)을 쓴다."""
    from remote_ci_monitor.core.render_text import render

    lz.app.sampler = None
    doc = lz.status()
    host = {
        "name": "coord",
        "source": "local",
        "sampled_at": doc["generated_at"],
        "age_seconds": 1,
        "interval_seconds": 5,
        "cores": 8,
        "load": [0.5],
        "cpu": {"busy": 3.0},
        "memory": {"used_bytes": 1, "total_bytes": 2},
    }
    doc["pools"][0]["hosts"] = [host]
    out = render(doc)
    head = out.splitlines()[0]
    assert "lanes 0/0" not in head, head
    assert "no local lanes" in head, head
    line = next(ln for ln in out.splitlines() if ln.startswith("host  coord"))
    assert "runs no jobs" in line, line
    # 레인이 있는 서버의 머리줄은 그대로다(비교 행)
    doc["server"]["lanes"] = 2
    assert "lanes 0/2 busy" in render(doc).splitlines()[0]
    assert "runs no jobs" not in render(doc)


def test_tl11_rcm_machines_marks_at_local_as_running_no_jobs(lz, home, monkeypatch, capsys):
    from remote_ci_monitor.cli import main

    monkeypatch.setenv("RCM_SERVER", f"http://127.0.0.1:{lz.port}")
    monkeypatch.setenv("RCM_TOKEN", lz.tokens["alice"])
    assert main(["machines"]) == 0
    out = capsys.readouterr().out
    line = next(ln for ln in out.splitlines() if "(runs no jobs)" in ln or "(down)" in ln)
    assert "(runs no jobs)" in line and "(down)" not in line, out
    assert "no report" not in line, line  # 재지 않는 머신의 판정을 「보고 없음」으로 세지 않는다
