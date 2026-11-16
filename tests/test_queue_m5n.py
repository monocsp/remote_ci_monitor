"""M5n 순수 계층 — 큐를 한 번에 계산(§7.2) · 그룹 범위(§6) · 새 이유(§7.3) · 호스트별 추정(§7.1).

이 파일의 절반은 **동치성 자물쇠**다(§18 위험 1 · §14 엣지 4). 풀마다 따로 돌던 `compute_queue` 를
전역 한 번으로 바꾸면 2,900여 개 테스트가 잠근 행이 흔들릴 수 있다. 그래서 고치기 **전에** 오늘의
코드로 무작위 픽스처 200개의 행을 `tests/fixtures/m5n_queue_golden.json` 으로 얼렸다. 호스트가
하나뿐인 풀만 있는 큐에서 새 전역 계산은 그 골든과 **같은 행**을 내야 한다.

골든을 다시 만들기(오늘의 규칙을 **일부러** 바꿀 때만 — 바꾼 이유를 커밋에 적는다)::

    ./.venv/bin/python tests/test_queue_m5n.py --write-golden

새 이름(`Placement` · `expected_on` · `REASON_NO_CAPABLE_MACHINE` …)은 모듈 속성으로 읽는다 —
그래야 구현 전에도 이 파일이 import 되고, 골든 생성기와 오늘 기준 자물쇠는 지금 돈다.
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from jobfactory import CFG, GATE, NOW, PRESETS, ago, job
from remote_ci_monitor.core import model as m
from remote_ci_monitor.core import queue as q
from remote_ci_monitor.core.model import (
    CANCELLING,
    DEFAULT_POOL,
    PHASE_EXECUTING,
    PHASE_MATERIALIZING,
    QUEUED,
    RUNNING,
    SUCCEEDED,
    UPLOADING,
    Job,
    Median,
    Preset,
    Progress,
    Requester,
    Source,
    Step,
    WorkerInfo,
)
from remote_ci_monitor.core.queue import StepMedian, compute_queue, split_by_pool

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "m5n_queue_golden.json"
GOLDEN_COUNT = 200

# ── 공용 도우미 ────────────────────────────────────────────────────────────────


def lane(worker, n=1, state="idle", *, pool=DEFAULT_POOL, host="h1", job_id=None, since=None):
    """레인 하나. 호스트 키를 단다(M5n `WorkerInfo.host_key`)."""
    w = WorkerInfo(
        lane=n,
        state=state,
        job_id=job_id,
        worker=worker,
        pool=pool,
        since=since or ago(minutes=30),
    )
    return replace(w, host_key=host)


def mk(id, key="gate:full", state=QUEUED, **kw):
    """`jobfactory.job` + M5n 새 필드(placement · display_pool · concurrency_scope · host_key …)."""
    new_fields = {
        k: kw.pop(k)
        for k in (
            "placement",
            "display_pool",
            "target_worker",
            "concurrency_scope",
            "host_key",
            "fingerprint_hash",
            "sample",
        )
        if k in kw
    }
    pool = kw.pop("pool", DEFAULT_POOL)
    worker_name = kw.pop("worker_name", None)
    j = replace(job(id, key, state, **kw), pool=pool, worker_name=worker_name)
    return replace(j, **new_fields) if new_fields else j


def by_id(rows):
    return {r.job.id: r for r in rows}


def med(seconds, n=5):
    return Median(seconds=float(seconds), wait_seconds=None, sample_count=n)


def secs(dt):
    return None if dt is None else (dt - NOW).total_seconds()


def run(jobs, wk, **kw):
    kw.setdefault("medians", {})
    return by_id(
        compute_queue(
            jobs,
            workers=wk,
            paused=kw.pop("paused", False),
            presets=kw.pop("presets", PRESETS),
            cfg=kw.pop("cfg", CFG),
            now=NOW,
            **kw,
        )
    )


def only(*workers):
    """잡마다 같은 후보 워커 집합을 주는 placement 콜백."""
    return lambda _job: q.Placement(workers=frozenset(workers))


# ── 상수 · 모델 (§7.3 · §11.4) ─────────────────────────────────────────────────


def test_new_reason_and_source_constants():
    assert m.REASON_NO_CAPABLE_MACHINE == "no_capable_machine"
    assert m.REASON_CHECK_FAILED == "check_failed"
    assert m.REASON_CHECK_PENDING == "check_pending"
    assert m.SOURCE_MEASURED_OTHER == "measured_other"
    assert (m.PLACEMENT_POOL, m.PLACEMENT_CAPABLE) == ("pool", "capable")
    assert (m.SCOPE_POOL, m.SCOPE_HOST, m.SCOPE_GLOBAL) == ("pool", "host", "global")


def test_actionable_reasons_order():
    """`worker_down` 바로 뒤에 새 둘. `check_pending` 은 스스로 풀려서 안 올린다."""
    assert m.ACTIONABLE_REASONS == (
        "worker_down",
        "no_capable_machine",
        "check_failed",
        "stuck",
        "upload_stalled",
        "not_scheduled",
        "blocked_by_group",
        "overdue",
        "paused",
    )
    assert m.REASON_CHECK_PENDING not in m.ACTIONABLE_REASONS


def test_job_preset_worker_defaults_keep_todays_behaviour():
    j = job(1)
    assert (j.placement, j.display_pool, j.target_worker) == ("pool", None, None)
    assert (j.concurrency_scope, j.host_key, j.fingerprint_hash, j.sample) == (
        "pool",
        None,
        None,
        True,
    )
    p = Preset(name="x", argv=("x",))
    assert (p.placement, p.concurrency_scope, p.checks, p.check_ref, p.sample) == (
        "pool",
        "pool",
        (),
        "HEAD",
        None,
    )
    assert WorkerInfo(lane=1, state="idle").host_key is None


def test_queue_row_placement_detail_defaults_to_empty():
    rows = run([mk(1)], [lane(None)])
    assert rows[1].placement_detail == ()


def test_confidence_of_measured_other_is_low_even_with_many_samples():
    assert q.confidence(m.SOURCE_MEASURED_OTHER, 50) == "low"


# ── expected_on — 그 호스트 → 다른 호스트들 → preset → default (§7.1 · 엣지 12) ─────────


def test_expected_on_uses_the_hosts_own_median():
    hm = {"hA": {"gate:full": med(100, 5)}, "hB": {"gate:full": med(900, 9)}}
    assert q.expected_on("gate:full", GATE, "hA", {}, hm, CFG) == (100.0, "measured", 5)


def test_expected_on_falls_back_to_another_host_as_measured_other():
    hm = {"hB": {"gate:full": med(700, 4)}}
    assert q.expected_on("gate:full", GATE, "hA", {}, hm, CFG) == (700.0, "measured_other", 4)


def test_expected_on_own_host_below_min_samples_falls_back_to_other_host():
    hm = {"hA": {"gate:full": med(100, 1)}, "hB": {"gate:full": med(700, 4)}}
    assert q.expected_on("gate:full", GATE, "hA", {}, hm, CFG) == (700.0, "measured_other", 4)


def test_expected_on_other_hosts_below_min_samples_do_not_count():
    hm = {"hA": {"gate:full": med(100, 1)}, "hB": {"gate:full": med(700, 1)}}
    assert q.expected_on("gate:full", GATE, "hA", {}, hm, CFG) == (480.0, "preset", 1)


def test_expected_on_takes_the_median_across_other_hosts():
    hm = {
        "hB": {"gate:full": med(100, 3)},
        "hC": {"gate:full": med(200, 3)},
        "hD": {"gate:full": med(900, 3)},
    }
    seconds, source, _n = q.expected_on("gate:full", GATE, "hA", {}, hm, CFG)
    assert (seconds, source) == (200.0, "measured_other")


def test_expected_on_without_any_sample_uses_preset_then_default():
    no_expect = Preset(name="lint", argv=("x",))
    assert q.expected_on("gate:full", GATE, "hA", {}, {}, CFG) == (480.0, "preset", 0)
    assert q.expected_on("lint", no_expect, "hA", {}, {}, CFG) == (600.0, "default", 0)


def test_expected_on_without_host_medians_is_expected_for():
    flat = {"gate:full": med(400, 7)}
    assert q.expected_on("gate:full", GATE, "hA", flat, None, CFG) == q.expected_for(
        "gate:full", GATE, flat, CFG
    )


# ── sample = false 는 표본이 아니다 (§7.4) ─────────────────────────────────────


def test_medians_from_ignores_jobs_marked_not_a_sample():
    real = [
        mk(i, state=SUCCEEDED, created_min=12, started_min=10, finished_min=5) for i in (1, 2, 3)
    ]
    checks = [
        mk(i, state=SUCCEEDED, created_min=22, started_min=20, finished_min=5, sample=False)
        for i in (4, 5)
    ]
    got = q.medians_from(real + checks, NOW, CFG)
    assert got["gate:full"].seconds == 300.0
    assert got["gate:full"].sample_count == 3


def test_medians_from_only_check_jobs_yields_no_median():
    checks = [
        mk(i, state=SUCCEEDED, created_min=22, started_min=20, finished_min=5, sample=False)
        for i in (1, 2, 3)
    ]
    assert q.medians_from(checks, NOW, CFG) == {}


# ── 전역 계산 · placement=None — 옛 규칙은 같은 풀의 레인 ──────────────────────────


def test_global_without_placement_keeps_jobs_on_their_own_pools_lanes():
    """풀 a 의 레인은 300초 뒤에 빈다. 풀 b 의 빈 레인을 a 의 잡이 가로채면 안 된다."""
    busy = mk(1, state=RUNNING, created_min=6, started_min=5, lane=1, pool="a", worker_name="wa")
    wk = [
        lane("wa", 1, "busy", pool="a", host="hA", job_id=1),
        lane("wb", 1, "idle", pool="b", host="hB"),
    ]
    jobs = [busy, mk(2, "deploy-dev", pool="a"), mk(3, "deploy-dev", pool="b")]
    rows = run(jobs, wk, medians={"gate:full": med(600, 5)})
    assert rows[2].estimate.wait_seconds == 300.0
    assert rows[2].ahead_job_id == 1
    assert rows[3].estimate.wait_seconds == 0.0
    assert rows[3].ahead_job_id is None


def test_global_positions_are_numbered_per_pool():
    wk = [lane(None, 1, pool=DEFAULT_POOL), lane("w2", 1, pool="mac2", host="h1")]
    jobs = [mk(1), mk(2, pool="mac2"), mk(3), mk(4, pool="mac2"), mk(5, priority=1)]
    rows = run(jobs, wk)
    assert {i: rows[i].position for i in rows} == {5: 1, 1: 2, 3: 3, 2: 1, 4: 2}


def test_global_shared_flag_does_not_cross_pools_on_different_hosts():
    """다른 풀 · 다른 호스트에서 도는 잡끼리는 머신을 나눠 쓰지 않는다 — 전역으로 세면 배지가
    거짓으로 내려간다(M5f). 같은 호스트의 두 풀을 `shared` 로 볼지는 여기서 잠그지 않는다."""
    wk = [
        lane(None, 1, "busy", job_id=1),
        lane("w2", 1, "busy", pool="mac2", host="h2", job_id=2),
    ]
    jobs = [
        mk(1, state=RUNNING, created_min=3, started_min=2, lane=1),
        mk(2, state=RUNNING, created_min=3, started_min=2, lane=1, pool="mac2", worker_name="w2"),
    ]
    rows = run(jobs, wk, medians={"gate:full": med(600, 7)})
    assert rows[1].estimate.shared is False
    assert rows[2].estimate.shared is False


# ── 가장 일찍 끝나는 레인 (§7.2 · 완료 기준 1) ───────────────────────────────────


def _hetero(busy_median):
    """빠른 호스트 hA(게이트 100초)의 레인은 바쁘고, 느린 호스트 hB(1000초)는 비어 있다."""
    busy = mk(
        1,
        "busy-key",
        RUNNING,
        preset="gate",
        created_min=6,
        started_min=5,
        lane=1,
        pool="fast",
        worker_name="fast",
        host_key="hA",
    )
    wk = [
        lane("fast", 1, "busy", pool="fast", host="hA", job_id=1),
        lane("slow", 1, "idle", pool="slow", host="hB"),
    ]
    hm = {
        "hA": {"busy-key": med(busy_median, 5), "gate:full": med(100, 5)},
        "hB": {"gate:full": med(1000, 5)},
    }
    return busy, wk, hm


def test_earliest_finish_waits_for_the_fast_host():
    busy, wk, hm = _hetero(busy_median=600)  # 300초 뒤에 빈다 → 400 < 1000
    jobs = [busy, mk(2, placement="capable")]
    rows = run(jobs, wk, placement=only("fast", "slow"), host_medians=hm)
    r = rows[2]
    assert r.estimate.wait_seconds == 300.0
    assert secs(r.estimate.finish_at) == 400.0
    assert r.estimate.expected_seconds == 100.0
    assert r.estimate.source == "measured"
    assert r.ahead_job_id == 1
    assert r.position == 1


def test_earliest_finish_takes_the_free_slow_host_when_fast_is_long_busy():
    busy, wk, hm = _hetero(busy_median=1800)  # 1500초 뒤에 빈다 → 1600 > 1000
    jobs = [busy, mk(2, placement="capable")]
    rows = run(jobs, wk, placement=only("fast", "slow"), host_medians=hm)
    r = rows[2]
    assert r.estimate.wait_seconds == 0.0
    assert secs(r.estimate.finish_at) == 1000.0
    assert r.estimate.expected_seconds == 1000.0
    assert r.ahead_job_id is None


def test_earliest_finish_chains_on_the_fast_host():
    busy, wk, hm = _hetero(busy_median=600)
    jobs = [busy, mk(2, placement="capable"), mk(3, placement="capable")]
    rows = run(jobs, wk, placement=only("fast", "slow"), host_medians=hm)
    assert secs(rows[2].estimate.finish_at) == 400.0
    assert rows[3].estimate.wait_seconds == 400.0  # 500 < 1000 — 다시 빠른 쪽
    assert secs(rows[3].estimate.finish_at) == 500.0
    assert rows[3].ahead_job_id == 2


def test_busy_job_estimate_uses_its_own_host():
    busy, wk, hm = _hetero(busy_median=600)
    rows = run([busy], wk, host_medians=hm)
    assert rows[1].estimate.expected_seconds == 600.0
    assert rows[1].estimate.source == "measured"
    assert rows[1].estimate.remaining_seconds == 300.0


def test_busy_job_on_a_host_without_samples_is_measured_other():
    busy, wk, _ = _hetero(busy_median=600)
    busy = replace(busy, host_key="hB")
    rows = run([busy], wk, host_medians={"hA": {"busy-key": med(600, 5)}})
    assert rows[1].estimate.source == "measured_other"
    assert rows[1].estimate.expected_seconds == 600.0
    assert q.confidence(rows[1].estimate.source, rows[1].estimate.sample_count) == "low"


def test_waiting_job_landing_on_a_host_without_samples_is_measured_other():
    wk = [lane("slow", 1, "idle", pool="slow", host="hB")]
    rows = run(
        [mk(1, placement="capable")],
        wk,
        placement=only("slow"),
        host_medians={"hA": {"gate:full": med(100, 5)}},
    )
    assert (rows[1].estimate.expected_seconds, rows[1].estimate.source) == (
        100.0,
        "measured_other",
    )
    assert secs(rows[1].estimate.finish_at) == 100.0


def test_placement_excludes_an_idle_worker_that_is_not_a_candidate():
    """엣지 5(큐 쪽) — 보고 없는 워커는 후보가 아니다. 그 빈 레인으로 ETA 를 짓지 않는다."""
    busy = mk(1, state=RUNNING, created_min=6, started_min=5, lane=1, worker_name="a")
    busy = replace(busy, host_key="h1")
    wk = [lane("a", 1, "busy", host="h1", job_id=1), lane("b", 1, "idle", host="h2")]
    rows = run(
        [busy, mk(2, placement="capable")],
        wk,
        placement=only("a"),
        medians={"gate:full": med(600, 5)},
    )
    assert rows[2].estimate.wait_seconds == 300.0
    assert rows[2].ahead_job_id == 1


def test_global_order_is_one_queue_across_display_pools():
    """로컬 레인 하나를 기본 풀 잡과 `any-capable` 잡이 claim 순서(우선순위·id)대로 나눠 쓴다."""
    wk = [lane(None, 1, "idle", host="h1")]
    jobs = [
        mk(1, "deploy-dev"),
        mk(2, "deploy-dev", pool="mac2", placement="capable"),
        mk(3, "deploy-dev"),
    ]
    rows = run(jobs, wk, placement=only(None))
    assert [rows[i].estimate.wait_seconds for i in (1, 2, 3)] == [0.0, 900.0, 1800.0]
    assert (rows[1].position, rows[2].position, rows[3].position) == (1, 1, 2)


def test_display_pool_decides_the_position_of_a_waiting_capable_job():
    wk = [lane(None, 1, "idle", host="h1")]
    jobs = [
        mk(1, pool="mac2"),
        mk(2, pool="mac2", display_pool=DEFAULT_POOL, placement="capable"),
        mk(3),
    ]
    rows = run(jobs, wk, placement=only(None))
    assert (rows[1].position, rows[2].position, rows[3].position) == (1, 1, 2)


# ── 동시 실행 그룹의 범위 (§6 · 엣지 3) ──────────────────────────────────────────


def _same_host(scope_busy, scope_waiting, *, waiting_pool="mac2", waiting_host="h1"):
    """운영 모양 — 로컬 레인(기본 풀)과 mac2-worker 가 같은 호스트 h1 위에 있다."""
    busy = mk(
        1,
        "qa",
        RUNNING,
        preset="qa",
        created_min=5,
        started_min=4,
        lane=1,
        group="sim",
        concurrency_scope=scope_busy,
        host_key="h1",
    )
    waiter = mk(
        2,
        "qa",
        preset="qa",
        group="sim",
        pool=waiting_pool,
        concurrency_scope=scope_waiting,
    )
    wk = [
        lane(None, 1, "busy", host="h1", job_id=1),
        lane("w2", 1, "idle", pool=waiting_pool, host=waiting_host),
    ]
    return run([busy, waiter], wk)  # qa 는 프리셋 540초 → 도는 잡은 300초 남았다


def test_host_scope_blocks_across_pools_on_the_same_host():
    r = _same_host("host", "host")[2]
    assert r.reason == "blocked_by_group"
    assert r.blocked_by.job_id == 1
    assert r.blocked_by.group == "sim"
    assert r.estimate.wait_seconds == 300.0
    assert secs(r.estimate.finish_at) == 840.0


def test_pool_scope_does_not_block_across_pools_even_on_the_same_host():
    r = _same_host("pool", "pool")[2]
    assert r.reason == "waiting_for_lane"
    assert r.blocked_by is None
    assert r.estimate.wait_seconds == 0.0
    assert secs(r.estimate.finish_at) == 540.0


def test_host_scope_does_not_block_another_host():
    r = _same_host("host", "host", waiting_pool="lab", waiting_host="h2")[2]
    assert r.blocked_by is None
    assert r.estimate.wait_seconds == 0.0


def test_global_scope_blocks_another_host_and_pool():
    r = _same_host("global", "global", waiting_pool="lab", waiting_host="h2")[2]
    assert r.reason == "blocked_by_group"
    assert r.blocked_by.job_id == 1
    assert r.estimate.wait_seconds == 300.0


def test_scope_of_the_waiting_job_decides_like_claim_does():
    """claim 의 배제는 **후보 잡의 범위**로 판정한다(§6). ETA 도 같은 답이어야 한다."""
    r = _same_host("pool", "global", waiting_pool="lab", waiting_host="h2")[2]
    assert r.reason == "blocked_by_group"
    assert r.estimate.wait_seconds == 300.0
    r = _same_host("global", "pool", waiting_pool="lab", waiting_host="h2")[2]
    assert r.blocked_by is None
    assert r.estimate.wait_seconds == 0.0


def test_host_scope_chains_waiting_jobs_across_pools():
    wk = [lane(None, 1, "idle", host="h1"), lane("w2", 1, "idle", pool="mac2", host="h1")]
    jobs = [
        mk(1, "qa", preset="qa", group="sim", concurrency_scope="host"),
        mk(2, "qa", preset="qa", group="sim", concurrency_scope="host", pool="mac2"),
    ]
    rows = run(jobs, wk)
    assert rows[1].estimate.wait_seconds == 0.0
    assert rows[2].estimate.wait_seconds == 540.0
    assert secs(rows[2].estimate.finish_at) == 1080.0
    assert rows[2].reason == "waiting_for_lane"  # 막는 것이 도는 잡이 아니다


def test_pool_scope_does_not_chain_waiting_jobs_across_pools():
    wk = [lane(None, 1, "idle", host="h1"), lane("w2", 1, "idle", pool="mac2", host="h1")]
    jobs = [
        mk(1, "qa", preset="qa", group="sim"),
        mk(2, "qa", preset="qa", group="sim", pool="mac2"),
    ]
    rows = run(jobs, wk)
    assert rows[2].estimate.wait_seconds == 0.0


# ── 새 이유 — 맞는 머신이 없다 · 점검 (§7.3 · S4) ────────────────────────────────

NOTES = (
    ("mbp", "mbp", "ineligible", "tool_missing", {"tools": ["fvm"]}),
    ("mac2-worker", "mac2-worker", "down", "worker_down", {}),
)


def _notes(spec=NOTES):
    return tuple(
        q.PlacementNote(worker=w, display_name=d, state=s, code=c, detail=x)
        for w, d, s, c, x in spec
    )


def _blocked(reason, *, paused=False, first_state=QUEUED):
    """#1 은 후보가 하나도 없는 잡(reason), #2 는 5분 기다린 기본 풀 잡.

    로컬 레인 하나가 30분째 비어 있다.
    """
    wk = [lane(None, 1, "idle", host="h1")]
    first = mk(1, "deploy-dev", first_state, placement="capable")
    if first_state == UPLOADING:
        first = replace(first, source=replace(first.source, last_received_at=NOW))
    jobs = [first, mk(2, "deploy-dev", created_min=5)]
    nothing = q.Placement(workers=frozenset(), reason=reason, notes=_notes())
    local = q.Placement(workers=frozenset({None}))
    return run(jobs, wk, placement=lambda j: nothing if j.id == 1 else local, paused=paused)


def test_no_capable_machine_row_has_no_eta_and_carries_the_notes():
    rows = _blocked(m.REASON_NO_CAPABLE_MACHINE)
    r = rows[1]
    assert r.reason == "no_capable_machine"
    assert r.estimate.wait_seconds is None
    assert r.estimate.finish_at is None
    assert r.ahead_job_id is None
    assert r.position == 1
    assert r.placement_detail == _notes()


def test_job_without_candidates_does_not_take_a_lane_from_the_job_behind():
    rows = _blocked(m.REASON_NO_CAPABLE_MACHINE)
    r = rows[2]
    assert r.estimate.wait_seconds == 0.0
    assert secs(r.estimate.finish_at) == 900.0
    assert r.reason == "not_scheduled"  # 빈 레인을 #1 이 「차지」했다고 세지 않는다
    assert r.position == 2
    assert r.placement_detail == ()


def test_check_failed_reason_passes_through():
    r = _blocked(m.REASON_CHECK_FAILED)[1]
    assert r.reason == "check_failed"
    assert r.estimate.finish_at is None


def test_check_pending_reason_passes_through():
    r = _blocked(m.REASON_CHECK_PENDING)[1]
    assert r.reason == "check_pending"
    assert r.estimate.finish_at is None


def test_paused_beats_no_capable_machine():
    r = _blocked(m.REASON_NO_CAPABLE_MACHINE, paused=True)[1]
    assert r.reason == "paused"
    assert r.placement_detail == _notes()


def test_uploading_beats_no_capable_machine():
    r = _blocked(m.REASON_NO_CAPABLE_MACHINE, first_state=UPLOADING)[1]
    assert r.reason == "uploading"


def test_candidates_all_down_is_worker_down():
    wk = [lane("mbp", 1, "down", pool="mbp", host="h2")]
    rows = run([mk(1, placement="capable")], wk, placement=only("mbp"))
    assert rows[1].reason == "worker_down"
    assert rows[1].estimate.finish_at is None


def test_placement_detail_is_capped_at_eight():
    spec = tuple((f"w{i}", f"w{i}", "unknown", "report_missing", {}) for i in range(10))
    wk = [lane(None, 1, "idle", host="h1")]
    nothing = q.Placement(
        workers=frozenset(), reason=m.REASON_NO_CAPABLE_MACHINE, notes=_notes(spec)
    )
    rows = run([mk(1, placement="capable")], wk, placement=lambda _j: nothing)
    assert rows[1].placement_detail == _notes(spec)[:8]


def test_busy_rows_have_no_placement_detail():
    wk = [lane(None, 1, "busy", host="h1", job_id=1)]
    busy = replace(mk(1, state=RUNNING, created_min=2, started_min=1, lane=1), host_key="h1")
    rows = run([busy], wk, placement=only(None))
    assert rows[1].placement_detail == ()


# ── 단계 중앙값은 다른 호스트로 물러나지 않는다 (§7.1 · 엣지 12 · 뮤테이션 ⑤) ─────────


def _slow_host_step(host_step_medians, step_medians=None):
    """hB 에서 `build` 단계가 2000초째 돈다. 출력은 10분째 없다. 잡 기대 3600초(hB 실측)."""
    busy = mk(
        1,
        "gate:full",
        RUNNING,
        created_min=40,
        started_min=35,
        lane=1,
        worker_name="slow",
        pool="slow",
        host_key="hB",
        last_output_at=ago(minutes=10),
        phase=PHASE_EXECUTING,
    )
    progress = Progress(
        phase=PHASE_EXECUTING,
        steps=(
            Step(index=1, name="setup", state="done", ok=True, seconds=12.0),
            Step(index=2, name="build", state="running", ok=None, seconds=None),
        ),
        steps_done=1,
        current_index=2,
        current_name="build",
        current_seconds=2000.0,
    )
    wk = [lane("slow", 1, "busy", pool="slow", host="hB", job_id=1)]
    return run(
        [busy],
        wk,
        host_medians={"hB": {"gate:full": med(3600, 5)}},
        progress={1: progress},
        host_step_medians=host_step_medians,
        step_medians=step_medians,
    )[1]


def test_step_median_of_another_host_does_not_make_stuck():
    r = _slow_host_step({("hA", "gate:full"): {"build": StepMedian(seconds=100.0, sample_count=5)}})
    assert r.estimate.stuck is False
    assert r.estimate.stuck_code is None
    assert r.estimate.step_expected_seconds is None
    assert r.estimate.quiet is True
    assert r.reason == "quiet"


def test_legacy_step_medians_are_ignored_when_host_tables_are_given():
    r = _slow_host_step(
        {},
        step_medians={"gate:full": {"build": StepMedian(seconds=100.0, sample_count=5)}},
    )
    assert r.estimate.stuck is False
    assert r.reason == "quiet"


def test_step_median_of_the_same_host_still_judges_stuck():
    r = _slow_host_step({("hB", "gate:full"): {"build": StepMedian(seconds=100.0, sample_count=5)}})
    assert r.estimate.stuck is True
    assert r.estimate.stuck_code == "over_step"
    assert r.estimate.step_expected_seconds == 100.0
    assert r.reason == "stuck"


# ── 동치성 골든 (§12 · §18 · 엣지 4) ─────────────────────────────────────────────
#
# 생성기는 **무작위지만 결정적**이다(`random.Random(seed)`). 픽스처 규칙:
# - 풀 2~3개(`default` + `mac2`/`lab` 중 1~2). 풀마다 호스트 하나. 기본 풀은 로컬 레인 1~3 +
#   가끔 원격 `dflt-w`, 다른 풀은 원격 워커 1~2개(레인 1~2).
#   **풀마다 워커가 적어도 하나**다(down 일 수 있다).
# - key 는 풀마다 따로(`<pool>:<preset>:<n>`) — 그래서 다른 풀의 실측이 끼어드는 `measured_other`
#   가 생기지 않는다(그건 동치성이 아니라 새 규칙이다).
# - 도는 잡(running·cancelling·materializing, 출력이 조용한 것, 단계 진행), 대기 잡(queued·
#   uploading · 우선순위 셋 · 그룹 `sim`/`gate`), down · held 레인, paused 를 섞는다.

GOLDEN_PRESETS = {**PRESETS, "lint": Preset(name="lint", argv=("make", "lint"))}
_EXTRA_POOLS = ("mac2", "lab")
_GROUPS = (None, None, "sim", "gate")


def _r1(x: float) -> float:
    return round(x, 1)


def golden_spec(seed: int) -> dict[str, Any]:
    """픽스처 하나의 JSON 명세 — 원시값만. `golden_build` 가 객체로 바꾼다."""
    rnd = random.Random(seed)
    pools = [DEFAULT_POOL, *sorted(rnd.sample(_EXTRA_POOLS, rnd.randint(1, 2)))]
    lanes: list[tuple[str | None, int, str]] = []
    for n in range(1, rnd.randint(1, 3) + 1):
        lanes.append((None, n, DEFAULT_POOL))
    if rnd.random() < 0.4:
        for n in range(1, rnd.randint(1, 2) + 1):
            lanes.append(("dflt-w", n, DEFAULT_POOL))
    for pool in pools[1:]:
        names = [f"{pool}-w"] + ([f"{pool}-w2"] if rnd.random() < 0.3 else [])
        for name in names:
            for n in range(1, rnd.randint(1, 2) + 1):
                lanes.append((name, n, pool))
    remote = sorted({w for w, _, _ in lanes if w is not None})
    down = {w for w in remote if rnd.random() < 0.2}
    presets = sorted(GOLDEN_PRESETS)

    def key_for(pool: str, preset: str) -> str:
        return f"{pool}:{preset}:{rnd.randint(0, 1)}"

    jobs: list[dict[str, Any]] = []
    workers: list[dict[str, Any]] = []
    for worker, n, pool in lanes:
        w: dict[str, Any] = {"worker": worker, "lane": n, "pool": pool, "since_min": 0.0}
        has_job = False
        if worker in down:
            w["state"] = "down"
            has_job = rnd.random() < 0.25  # 워커는 죽었는데 DB 의 잡은 아직 running
        elif rnd.random() < 0.5:
            w["state"] = "busy"
            has_job = True
        elif rnd.random() < 0.2:
            w["state"] = "held"
        else:
            w["state"] = "idle"
            w["since_min"] = _r1(rnd.uniform(0, 60))
        workers.append(w)
        if not has_job:
            continue
        preset = rnd.choice(presets)
        started = _r1(rnd.uniform(0.5, 40))
        jobs.append(
            {
                "pool": pool,
                "preset": preset,
                "key": key_for(pool, preset),
                "state": CANCELLING if rnd.random() < 0.1 else RUNNING,
                "priority": rnd.choice((-1, 0, 0, 1)),
                "group": rnd.choice(_GROUPS),
                "created_min": _r1(started + rnd.uniform(0, 10)),
                "queued_min": None,
                "started_min": started,
                "worker": worker,
                "lane": n,
                "last_output_min": rnd.choice((0.02, _r1(rnd.uniform(0, 15)))),
                "phase": PHASE_MATERIALIZING if rnd.random() < 0.08 else PHASE_EXECUTING,
                "received_min": None,
                "step_seconds": _r1(rnd.uniform(5, 2500)) if rnd.random() < 0.5 else None,
                "_lane_ref": len(workers) - 1 if w["state"] == "busy" else None,
            }
        )
    for pool in pools:
        for _ in range(rnd.randint(0, 6)):
            preset = rnd.choice(presets)
            uploading = rnd.random() < 0.1
            created = _r1(rnd.uniform(0, 30))
            jobs.append(
                {
                    "pool": pool,
                    "preset": preset,
                    "key": key_for(pool, preset),
                    "state": UPLOADING if uploading else QUEUED,
                    "priority": rnd.choice((-1, 0, 0, 1)),
                    "group": rnd.choice(_GROUPS),
                    "created_min": created,
                    "queued_min": None if uploading else _r1(created * rnd.random()),
                    "started_min": None,
                    "worker": None,
                    "lane": None,
                    "last_output_min": None,
                    "phase": None,
                    "received_min": _r1(rnd.uniform(0, 3)) if uploading else None,
                    "step_seconds": None,
                    "_lane_ref": None,
                }
            )
    # id 는 섞은 순서로 — 풀이 번갈아 나오게(전역 정렬이 풀을 넘나드는지 본다)
    rnd.shuffle(jobs)
    for i, j in enumerate(jobs, start=1):
        j["id"] = i
        ref = j.pop("_lane_ref")
        if ref is not None:
            workers[ref]["job_id"] = i
    medians: dict[str, dict[str, list[float]]] = {}
    steps: dict[str, list[float]] = {}
    for pool in pools:
        table: dict[str, list[float]] = {}
        for preset in presets:
            for variant in (0, 1):
                key = f"{pool}:{preset}:{variant}"
                r = rnd.random()
                if r < 0.3:
                    pass
                elif r < 0.45:
                    table[key] = [_r1(rnd.uniform(60, 1500)), 1]
                else:
                    table[key] = [_r1(rnd.uniform(60, 1500)), rnd.randint(2, 8)]
                if rnd.random() < 0.5:
                    steps[key] = [_r1(rnd.uniform(30, 900)), rnd.randint(3, 6)]
        medians[pool] = table
    return {
        "paused": rnd.random() < 0.1,
        "pools": pools,
        "workers": workers,
        "jobs": jobs,
        "medians": medians,
        "steps": steps,
    }


def spec_digest(spec: dict[str, Any]) -> str:
    text = json.dumps(spec, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _minutes(value: float | None):
    return None if value is None else ago(minutes=value)


def golden_build(spec: dict[str, Any]):
    """명세 → (잡, 워커, 풀별 중앙값, 단계 중앙값, 진행) — **오늘의 모델 필드만** 쓴다."""
    jobs: list[Job] = []
    progress: dict[int, Progress] = {}
    for s in spec["jobs"]:
        received = _minutes(s["received_min"])
        jobs.append(
            Job(
                id=s["id"],
                preset=s["preset"],
                inputs={},
                key=s["key"],
                concurrency_group=s["group"],
                source=Source(mode="tree", tree_hash=f"t{s['id']}", last_received_at=received),
                requester=Requester(name="alice", label="alice@laptop"),
                state=s["state"],
                created_at=ago(minutes=s["created_min"]),
                queued_at=_minutes(s["queued_min"]),
                started_at=_minutes(s["started_min"]),
                lane=s["lane"],
                phase=s["phase"],
                last_output_at=_minutes(s["last_output_min"]),
                priority=s["priority"],
                pool=s["pool"],
                worker_name=s["worker"],
            )
        )
        if s["step_seconds"] is not None:
            progress[s["id"]] = Progress(
                phase=PHASE_EXECUTING,
                steps=(
                    Step(index=1, name="setup", state="done", ok=True, seconds=12.0),
                    Step(index=2, name="build", state="running", ok=None, seconds=None),
                ),
                steps_done=1,
                current_index=2,
                current_name="build",
                current_seconds=s["step_seconds"],
            )
    workers = [
        WorkerInfo(
            lane=w["lane"],
            state=w["state"],
            job_id=w.get("job_id"),
            worker=w["worker"],
            pool=w["pool"],
            since=ago(minutes=w["since_min"]),
        )
        for w in spec["workers"]
    ]
    pool_medians = {
        pool: {k: Median(seconds=v[0], wait_seconds=None, sample_count=v[1]) for k, v in t.items()}
        for pool, t in spec["medians"].items()
    }
    steps = {
        k: {"build": StepMedian(seconds=v[0], sample_count=v[1])} for k, v in spec["steps"].items()
    }
    return jobs, workers, pool_medians, steps, progress


def row_record(row) -> dict[str, Any]:
    est = row.estimate
    return {
        "id": row.job.id,
        "position": row.position,
        "reason": row.reason,
        "lane": row.lane,
        "ahead_job_id": row.ahead_job_id,
        "blocked_by": row.blocked_by.job_id if row.blocked_by else None,
        "wait_seconds": est.wait_seconds,
        "finish_at": est.finish_at.isoformat() if est.finish_at else None,
        "expected_seconds": est.expected_seconds,
        "source": est.source,
        "sample_count": est.sample_count,
        "remaining_seconds": est.remaining_seconds,
        "shared": est.shared,
        "quiet": est.quiet,
        "stuck_code": est.stuck_code,
    }


def per_pool_rows(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """**오늘의 서버** 그대로 — `server._queue_rows` 가 풀마다 `compute_queue` 를 부르는 모양."""
    jobs, workers, pool_medians, steps, progress = golden_build(spec)
    rows = []
    for name, pool_jobs in split_by_pool(jobs).items():
        rows.extend(
            compute_queue(
                pool_jobs,
                workers=[w for w in workers if w.pool == name],
                paused=spec["paused"],
                medians=pool_medians.get(name, {}),
                presets=GOLDEN_PRESETS,
                cfg=CFG,
                now=NOW,
                progress=progress,
                step_medians=steps,
            )
        )
    return sorted((row_record(r) for r in rows), key=lambda r: r["id"])


def _merged(pool_medians):
    # key 가 풀마다 다르므로 합쳐도 겹치지 않는다
    return {k: v for table in pool_medians.values() for k, v in table.items()}


def global_rows_legacy(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """새 전역 계산 · `placement=None` · 새 kwarg 없이 — 「기존 호출은 그대로 돈다」."""
    jobs, workers, pool_medians, steps, progress = golden_build(spec)
    rows = compute_queue(
        jobs,
        workers=workers,
        paused=spec["paused"],
        medians=_merged(pool_medians),
        presets=GOLDEN_PRESETS,
        cfg=CFG,
        now=NOW,
        progress=progress,
        step_medians=steps,
    )
    return sorted((row_record(r) for r in rows), key=lambda r: r["id"])


def global_rows_placed(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """새 전역 계산 · 같은 풀 규칙의 placement · 호스트별 표(풀마다 호스트 `h-<pool>` 하나)."""
    jobs, workers, pool_medians, steps, progress = golden_build(spec)
    workers = [replace(w, host_key=f"h-{w.pool}") for w in workers]
    jobs = [replace(j, host_key=f"h-{j.pool}") if j.is_busy else j for j in jobs]
    by_pool: dict[str, frozenset[str | None]] = {}
    for w in workers:
        by_pool[w.pool] = by_pool.get(w.pool, frozenset()) | {w.worker}

    def same_pool(j: Job):
        return q.Placement(workers=by_pool.get(j.pool, frozenset()))

    rows = compute_queue(
        jobs,
        workers=workers,
        paused=spec["paused"],
        medians=_merged(pool_medians),
        presets=GOLDEN_PRESETS,
        cfg=CFG,
        now=NOW,
        progress=progress,
        placement=same_pool,
        host_medians={f"h-{pool}": table for pool, table in pool_medians.items()},
        # key 의 앞머리가 그 풀이다(`<pool>:<preset>:<n>`)
        host_step_medians={(f"h-{k.split(':', 1)[0]}", k): t for k, t in steps.items()},
    )
    return sorted((row_record(r) for r in rows), key=lambda r: r["id"])


GOLDEN_COLUMNS = (
    "id",
    "position",
    "reason",
    "lane",
    "ahead_job_id",
    "blocked_by",
    "wait_seconds",
    "finish_at",
    "expected_seconds",
    "source",
    "sample_count",
    "remaining_seconds",
    "shared",
    "quiet",
    "stuck_code",
)


def write_golden() -> None:
    """골든을 쓴다 — 픽스처 하나가 한 줄(diff 가 읽히게), 행은 `columns` 순서의 배열."""
    lines = []
    for seed in range(GOLDEN_COUNT):
        spec = golden_spec(seed)
        rows = [[r[c] for c in GOLDEN_COLUMNS] for r in per_pool_rows(spec)]
        fixture = {"seed": seed, "digest": spec_digest(spec), "rows": rows}
        lines.append(json.dumps(fixture, separators=(",", ":")))
    head = {
        "about": "M5n 동치성 골든 — tests/test_queue_m5n.py 의 golden_spec 이 만들고 "
        "M5n 이전 코드(풀별 compute_queue)가 계산했다. 손으로 고치지 않는다.",
        "now": NOW.isoformat(),
        "count": GOLDEN_COUNT,
        "columns": list(GOLDEN_COLUMNS),
    }
    body = ",\n".join(lines)
    text = json.dumps(head, ensure_ascii=False)[:-1] + ', "fixtures": [\n' + body + "\n]}\n"
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN.write_text(text)


def _golden() -> dict[str, Any]:
    """골든을 읽어 행을 dict 로 편다."""
    data = json.loads(GOLDEN.read_text())
    assert tuple(data["columns"]) == GOLDEN_COLUMNS
    for fx in data["fixtures"]:
        fx["rows"] = [dict(zip(GOLDEN_COLUMNS, r, strict=True)) for r in fx["rows"]]
    return data


def _mismatches(compute) -> list[str]:
    out = []
    for fx in _golden()["fixtures"]:
        spec = golden_spec(fx["seed"])
        got = {r["id"]: r for r in compute(spec)}
        for want in fx["rows"]:
            have = got.get(want["id"])
            if have != want:
                diff = {
                    k: (want[k], None if have is None else have.get(k))
                    for k in want
                    if have is None or have.get(k) != want[k]
                }
                out.append(f"seed {fx['seed']} job {want['id']}: {diff}")
        extra = set(got) - {r["id"] for r in fx["rows"]}
        if extra:
            out.append(f"seed {fx['seed']}: unexpected rows {sorted(extra)}")
    return out


def test_golden_fixture_matches_its_generator():
    """생성기가 흔들리면(파이썬 판 · 코드 수정) 골든과 다른 큐를 비교하게 된다 — 먼저 잡는다."""
    data = _golden()
    assert data["count"] == GOLDEN_COUNT == len(data["fixtures"])
    assert data["now"] == NOW.isoformat()
    for fx in data["fixtures"]:
        assert spec_digest(golden_spec(fx["seed"])) == fx["digest"], fx["seed"]


def test_golden_equals_per_pool_calls():
    """풀별 호출(placement=None, 풀 하나의 잡과 레인)은 M5n 뒤에도 오늘과 같은 답이다."""
    problems = _mismatches(per_pool_rows)
    assert problems == [], "\n".join(problems[:5])


def test_golden_covers_the_rules_it_locks():
    """골든이 규칙을 실제로 지나는지 — 이유 · 출처 · 차단 · 공유가 다 한 번 이상 나온다."""
    rows = [r for fx in _golden()["fixtures"] for r in fx["rows"]]
    reasons = {r["reason"] for r in rows}
    assert reasons >= {
        "running",
        "cancelling",
        "materializing",
        "stuck",
        "overdue",
        "quiet",
        "waiting_for_lane",
        "blocked_by_group",
        "not_scheduled",
        "held_by_load",
        "worker_down",
        "paused",
        "uploading",
        "upload_stalled",
    }
    assert {r["source"] for r in rows} == {"measured", "preset", "default"}
    assert {r["stuck_code"] for r in rows} >= {"over_step", "over_elapsed", None}
    assert any(r["shared"] for r in rows)
    assert any(r["ahead_job_id"] is not None for r in rows)
    waiting = [r for r in rows if r["position"] is not None]
    assert any(r["wait_seconds"] and r["wait_seconds"] > 0 for r in waiting)
    assert len(rows) > 1500


def test_global_call_without_placement_matches_golden():
    """엣지 4 — 모든 잡 · 모든 레인을 한 번에 넘겨도 풀별 계산과 같은 행."""
    problems = _mismatches(global_rows_legacy)
    assert problems == [], f"{len(problems)} rows differ:\n" + "\n".join(problems[:5])


def test_global_call_with_same_pool_placement_matches_golden():
    """엣지 4 — 새 kwarg(placement · host_medians · host_step_medians)로도 같은 행."""
    problems = _mismatches(global_rows_placed)
    assert problems == [], f"{len(problems)} rows differ:\n" + "\n".join(problems[:5])


if __name__ == "__main__":
    if sys.argv[1:] != ["--write-golden"]:
        raise SystemExit("usage: python tests/test_queue_m5n.py --write-golden")
    write_golden()
    print(f"wrote {GOLDEN}")
