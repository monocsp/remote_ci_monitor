"""스키마 v1 + 풀(M5b-1) — `pools[]` 가 풀마다 한 항목(기본 풀 먼저) · 큐/최근 행마다 `pool`
(추가 키) · 워커 없는 풀의 대기 잡은 `worker_down` + `finish_at`/`wait_seconds` null · 풀별 중앙값 ·
원격 풀은 `lanes 0` · `hosts []` · `schema_version` 은 그대로 1. 구현 전이라 빨간 것이 정상이다.

키 집합은 test_status_schema 의 ROW_KEYS/RECENT_KEYS/POOL_KEYS 를 가져와 `| {"pool"}` 로 비교한다
(기존 파일은 손대지 않는다). 풀별로 나누는 건 여기서 직접 한다 — `split_by_pool` 은 test_pools 몫.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from jobfactory import CFG, GATE, MEDIANS, NOW, PRESETS, QA, default_workers, job
from remote_ci_monitor import SCHEMA_VERSION
from remote_ci_monitor.core.model import (
    FAILED,
    RUNNING,
    Job,
    Median,
    Pool,
    QueueRow,
    ServerInfo,
    StatusModel,
    Transition,
    WorkerInfo,
)
from remote_ci_monitor.core.queue import compute_queue
from remote_ci_monitor.core.status import queue_row_json, recent_json, status_json
from test_status_schema import ESTIMATE_KEYS, POOL_KEYS, RECENT_KEYS, ROW_KEYS

LINUX_MEDIANS = {"gate:full": Median(seconds=120.0, wait_seconds=5.0, sample_count=3)}


def rows(
    jobs: Sequence[Job], *, wk: Sequence[WorkerInfo], medians: dict[str, Median] = MEDIANS
) -> list[QueueRow]:
    return compute_queue(
        jobs, workers=wk, paused=False, medians=medians, presets=PRESETS, cfg=CFG, now=NOW
    )


def pool(
    name: str,
    *,
    queue: Sequence[QueueRow] | None,
    recent: Sequence[Job] | None = (),
    medians: dict[str, Median] | None = None,
    lanes: int | None = None,
) -> Pool:
    """로컬(기본) 풀은 레인 1, 원격 풀은 레인 0 · hosts [] 가 M5b-1 의 모양이다."""
    return Pool(
        name=name,
        lanes=lanes if lanes is not None else (1 if name == "default" else 0),
        queue=None if queue is None else tuple(queue),
        queue_error=None,
        recent=None if recent is None else tuple(recent),
        recent_error=None,
        recent_count=8,
        medians=medians if medians is not None else {},
        medians_error=None,
        hosts=(),
        hosts_error=None,
    )


def model(*pools: Pool) -> StatusModel:
    wk = default_workers([412])
    server = ServerInfo(
        version="0.1.0",
        uptime_seconds=8123,
        lanes=len(wk),  # 로컬 레인 수 — 풀이 늘어도 그대로
        paused=None,
        last_error=None,
        workers=tuple(wk),
    )
    return StatusModel(
        generated_at=NOW,
        display_timezone=None,
        server=server,
        presets=(GATE, QA),
        pools=tuple(pools),
        base_url="http://macmini:8787",
    )


def default_jobs() -> list[Job]:
    return [job(412, state=RUNNING, created_min=2, started_min=1), job(413, created_min=1)]


def linux_jobs() -> list[Job]:
    return [
        job(414, created_min=1, pool="linux"),
        job(415, "deploy-dev", created_min=0.5, pool="linux"),
    ]


def finished(id: int, *, pool: str = "default") -> Job:
    return job(
        id,
        "gate:fast",
        FAILED,
        created_min=9,
        started_min=8,
        finished_min=6,
        exit_code=1,
        summary="2 tests failed",
        failed_step="test",
        timeout_seconds=1200,
        transitions=(Transition("queued", NOW), Transition("failed", NOW)),
        pool=pool,
    )


def two_pool_doc() -> dict[str, Any]:
    default = pool(
        "default",
        queue=rows(default_jobs(), wk=default_workers([412])),
        recent=[finished(411)],
        medians=MEDIANS,
    )
    linux = pool(
        "linux",
        queue=rows(linux_jobs(), wk=[], medians=LINUX_MEDIANS),  # 워커 없음
        recent=[finished(410, pool="linux")],
        medians=LINUX_MEDIANS,
    )
    return status_json(model(default, linux), log_tails={412: ["[test] 3/9"]})


# ── 모양 ─────────────────────────────────────────────────────────────────────


def test_two_pools_default_first_with_full_key_sets_and_json_serializable():
    doc = two_pool_doc()
    back = json.loads(json.dumps(doc))  # 실패하면 여기서 터진다
    assert back["schema_version"] == SCHEMA_VERSION == 1  # 추가 키만 — 버전은 그대로
    assert [p["name"] for p in back["pools"]] == ["default", "linux"]
    for p in back["pools"]:
        assert set(p) == POOL_KEYS  # `workers` 는 풀 JSON 에 없다
        assert [set(r) for r in p["queue"]] == [ROW_KEYS | {"pool"}] * len(p["queue"])
        assert [set(r) for r in p["recent"]] == [RECENT_KEYS | {"pool"}] * len(p["recent"])
        assert all(set(r["estimate"]) == ESTIMATE_KEYS for r in p["queue"])
    assert back["server"]["lanes"] == 1 and len(back["server"]["workers"]) == 1


def test_rows_and_recent_carry_their_pool():
    doc = two_pool_doc()
    default, linux = doc["pools"]
    assert [(r["id"], r["pool"]) for r in default["queue"]] == [(412, "default"), (413, "default")]
    assert [(r["id"], r["pool"]) for r in linux["queue"]] == [(414, "linux"), (415, "linux")]
    assert [(r["id"], r["pool"]) for r in default["recent"]] == [(411, "default")]
    assert [(r["id"], r["pool"]) for r in linux["recent"]] == [(410, "linux")]
    assert default["queue"][0]["log_tail"] == ["[test] 3/9"]  # 토큰 조건 log_tail 은 그대로


def test_remote_pool_has_zero_lanes_and_empty_hosts_but_the_same_sections():
    doc = two_pool_doc()
    default, linux = doc["pools"]
    assert default["lanes"] == 1 and linux["lanes"] == 0
    # 아직 워커 표본이 없다 — 실패가 아니다
    assert linux["hosts"] == [] and linux["hosts_error"] is None
    assert linux["queue_error"] is None and linux["recent_error"] is None
    assert linux["medians_error"] is None and linux["recent_count"] == 8


# ── 워커 없는 풀 ─────────────────────────────────────────────────────────────


def test_queued_job_in_a_pool_without_workers_is_worker_down_with_null_times():
    linux = two_pool_doc()["pools"][1]
    first, second = linux["queue"]
    assert first["id"] == 414 and first["state"] == "queued"
    assert first["reason"] == "worker_down" and first["position"] == 1
    assert first["estimate"]["finish_at"] is None and first["estimate"]["wait_seconds"] is None
    assert first["estimate"]["expected_seconds"] == 120  # 리눅스 풀의 실측 중앙값
    assert first["estimate"]["source"] == "measured" and first["estimate"]["confidence"] == "med"
    assert first["lane"] is None and first["ahead_job_id"] is None and first["progress"] is None
    assert second["reason"] == "worker_down" and second["position"] == 2
    assert second["estimate"]["finish_at"] is None


def test_default_pool_keeps_its_normal_reasons_next_to_a_down_pool():
    default = two_pool_doc()["pools"][0]
    running, waiting = default["queue"]
    assert running["id"] == 412 and running["reason"] == "running" and running["position"] is None
    assert running["estimate"]["finish_at"] is not None
    assert waiting["id"] == 413 and waiting["reason"] == "waiting_for_lane"
    assert waiting["position"] == 1 and waiting["ahead_job_id"] == 412
    assert waiting["estimate"]["finish_at"] is not None
    assert waiting["estimate"]["wait_seconds"] == 340  # 412 의 잔여(400 − 60)
    assert waiting["estimate"]["expected_seconds"] == 400  # 기본 풀의 중앙값


# ── 풀별 중앙값 ───────────────────────────────────────────────────────────────


def test_medians_are_per_pool_same_key_different_values():
    default, linux = two_pool_doc()["pools"]
    default_median = {"seconds": 400, "wait_seconds": 80, "sample_count": 7}
    linux_median = {"seconds": 120, "wait_seconds": 5, "sample_count": 3}
    assert default["medians"]["gate:full"] == default_median
    assert linux["medians"]["gate:full"] == linux_median
    assert "deploy-dev" in default["medians"] and "deploy-dev" not in linux["medians"]


# ── 행 단위 함수 ─────────────────────────────────────────────────────────────


def test_queue_row_json_and_recent_json_add_pool_and_default_it():
    row = rows([job(5, created_min=1)], wk=default_workers([]))[0]
    doc = queue_row_json(row, base_url="http://macmini:8787")
    assert doc["pool"] == "default" and set(doc) == ROW_KEYS | {"pool"}
    linux_row = replace(row, job=replace(row.job, pool="linux"))
    assert queue_row_json(linux_row)["pool"] == "linux"
    assert recent_json(finished(9))["pool"] == "default"
    assert recent_json(finished(9, pool="mac2"))["pool"] == "mac2"
    assert set(recent_json(finished(9))) == RECENT_KEYS | {"pool"}


def test_single_pool_document_keeps_its_shape():
    """회귀: 풀 하나일 때 화면은 그대로 — pools 한 개 · 이름 default · 행마다 pool 만 더 있다."""
    doc = status_json(model(pool("default", queue=rows(default_jobs(), wk=default_workers([412])))))
    assert len(doc["pools"]) == 1 and doc["pools"][0]["name"] == "default"
    assert all(r["pool"] == "default" for r in doc["pools"][0]["queue"])
    assert json.loads(json.dumps(doc))["schema_version"] == 1


def test_failed_sections_stay_null_plus_error_in_every_pool():
    broken = Pool(
        name="linux",
        lanes=0,
        queue=None,
        queue_error="database locked",
        recent=None,
        recent_error="db",
        recent_count=8,
        medians=None,
        medians_error="db",
        hosts=None,
        hosts_error="worker sampler missing",
    )
    doc = status_json(model(pool("default", queue=[]), broken))
    default, linux = doc["pools"]
    assert default["queue"] == [] and default["queue_error"] is None
    assert linux["queue"] is None and linux["queue_error"] == "database locked"
    assert linux["recent"] is None and linux["medians"] is None and linux["hosts"] is None
    assert linux["hosts_error"] == "worker sampler missing"
