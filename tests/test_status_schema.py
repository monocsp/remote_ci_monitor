"""스키마 v1 — json.dumps 가능 · null + *_error · pools 한 개 · position/log_tail 규칙 · 키 집합."""

import json

from jobfactory import CFG, GATE, MEDIANS, NOW, PRESETS, QA, ago, default_workers, job
from remote_ci_monitor import SCHEMA_VERSION
from remote_ci_monitor.core.model import (
    FAILED,
    QUEUED,
    RUNNING,
    HostSample,
    Paused,
    Pool,
    ServerInfo,
    StatusModel,
    Transition,
    WorkerInfo,
)
from remote_ci_monitor.core.queue import compute_queue
from remote_ci_monitor.core.status import iso, parse_iso, status_json

ROW_KEYS = {
    "id",
    "position",
    "priority",
    "pool",
    "preset",
    "key",
    "inputs",
    "concurrency_group",
    "requester",
    "joiners",
    "state",
    "reason",
    "lane",
    "ahead_job_id",
    "blocked_by",
    "cancel",
    "source",
    "created_at",
    "queued_at",
    "started_at",
    "estimate",
    "progress",
    "log_tail",
    "url",
}
ESTIMATE_KEYS = {
    "confidence",
    "expected_seconds",
    "source",
    "sample_count",
    "elapsed_seconds",
    "waited_seconds",
    "remaining_seconds",
    "wait_seconds",
    "overdue",
    "stuck",
    "finish_at",
}
RECENT_KEYS = {
    "pool",
    "id",
    "preset",
    "key",
    "inputs",
    "requester",
    "joiners",
    "state",
    "exit_code",
    "job_seconds",
    "waited_seconds",
    "created_at",
    "started_at",
    "finished_at",
    "summary",
    "failed_step",
    "cancelled_by",
    "timeout_seconds",
    "source",
    "transitions",
    "url",
}
POOL_KEYS = {
    "name",
    "lanes",
    "queue",
    "queue_error",
    "recent",
    "recent_count",
    "recent_error",
    "medians",
    "medians_error",
    "hosts",
    "hosts_error",
}


def model(
    *,
    queue=None,
    queue_error=None,
    recent=(),
    recent_error=None,
    hosts=(),
    hosts_error=None,
    medians=MEDIANS,
    medians_error=None,
    paused=None,
    workers=None,
):
    wk = workers or default_workers([412])
    server = ServerInfo(
        version="0.1.0",
        uptime_seconds=8123,
        lanes=len(wk),
        paused=paused,
        last_error=None,
        workers=tuple(wk),
    )
    pool = Pool(
        name="default",
        lanes=len(wk),
        queue=None if queue is None else tuple(queue),
        queue_error=queue_error,
        recent=None if recent is None else tuple(recent),
        recent_error=recent_error,
        recent_count=8,
        medians=medians,
        medians_error=medians_error,
        hosts=None if hosts is None else tuple(hosts),
        hosts_error=hosts_error,
    )
    return StatusModel(
        generated_at=NOW,
        display_timezone=None,
        server=server,
        presets=(GATE, QA),
        pools=(pool,),
        base_url="http://macmini:8787",
    )


def sample_rows():
    jobs = [job(412, state=RUNNING, created_min=2, started_min=1), job(413, created_min=1)]
    return compute_queue(
        jobs,
        workers=default_workers([412]),
        paused=False,
        medians=MEDIANS,
        presets=PRESETS,
        cfg=CFG,
        now=NOW,
    )


def test_status_is_json_serializable_with_expected_shape():
    failed = job(
        411,
        "gate:fast",
        FAILED,
        created_min=9,
        started_min=8,
        finished_min=6,
        exit_code=1,
        summary="2 tests failed",
        failed_step="test",
        timeout_seconds=1200,
        transitions=(Transition("queued", ago(minutes=9)), Transition("failed", ago(minutes=6))),
    )
    host = HostSample(
        name="macmini",
        source="local",
        sampled_at=ago(seconds=4),
        interval_seconds=5,
        os="darwin",
        cores=10,
        load=(3.48, 3.1, 2.9),
        cpu={"user": 17.0, "sys": 4.0, "idle": 79.0, "busy": 21.0},
        memory={"total_bytes": 24 * 10**9, "used_bytes": 14 * 10**9, "compressed_bytes": None},
        gpu={
            "util_pct": 13,
            "mem_used_bytes": 594411520,
            "mem_total_bytes": None,
            "source": "ioreg",
        },
    )
    doc = status_json(
        model(queue=sample_rows(), recent=[failed], hosts=[host]), log_tails={412: ["[test] 3/9"]}
    )
    text = json.dumps(doc)  # 실패하면 여기서 터진다
    back = json.loads(text)
    assert back["schema_version"] == SCHEMA_VERSION
    assert back["generated_at"] == "2026-09-04T00:52:12Z"
    assert set(back) == {
        "schema_version",
        "generated_at",
        "display_timezone",
        "server",
        "presets",
        "pools",
    }
    assert set(back["server"]) == {
        "version",
        "uptime_seconds",
        "lanes",
        "snapshot_cache",
        "notify_failures",
        "paused",
        "last_error",
        "sse_connections",
        "workers",
    }
    assert set(back["server"]["workers"][0]) == {
        "lane",
        "state",
        "job_id",
        "error",
        "since",
        "worker",  # M5b-2 추가 키
        "display_name",
    }
    assert len(back["pools"]) == 1
    pool = back["pools"][0]
    assert set(pool) == POOL_KEYS
    rows = pool["queue"]
    assert [set(r) for r in rows] == [ROW_KEYS, ROW_KEYS]
    assert set(rows[0]["estimate"]) == ESTIMATE_KEYS
    assert set(pool["recent"][0]) == RECENT_KEYS
    assert set(back["presets"][0]) == {
        "name",
        "description",
        "source_modes",
        "repo",
        "priority",
        "pool",
        "pools",
        "concurrency_group",
        "expected_seconds",
        "timeout_seconds",
        "inputs",
    }
    assert pool["medians"]["gate:full"] == {"seconds": 400, "wait_seconds": 80, "sample_count": 7}
    h = pool["hosts"][0]
    assert h["age_seconds"] == 4 and h["stale"] is False and h["memory"]["compressed_bytes"] is None


def test_position_is_null_for_running_and_log_tail_only_when_given():
    doc = status_json(model(queue=sample_rows()), log_tails={412: ["line"]})
    rows = doc["pools"][0]["queue"]
    assert (
        rows[0]["state"] == "running"
        and rows[0]["position"] is None
        and rows[0]["log_tail"] == ["line"]
    )
    assert rows[1]["state"] == "queued" and rows[1]["position"] == 1 and rows[1]["log_tail"] is None
    assert rows[1]["progress"] is None  # 0/0 금지
    assert rows[0]["progress"] is None or rows[0]["progress"]["timing"] == "as_received"
    assert rows[0]["url"] == "http://macmini:8787/#/jobs/412"


def test_failed_sections_are_null_plus_error_not_empty():
    doc = status_json(
        model(
            queue=None,
            queue_error="database locked",
            recent=None,
            recent_error="db",
            hosts=None,
            hosts_error="sampler crashed",
            medians=None,
            medians_error="db",
        )
    )
    pool = doc["pools"][0]
    assert pool["queue"] is None and pool["queue_error"] == "database locked"
    assert pool["recent"] is None and pool["recent_error"] == "db"
    assert pool["hosts"] is None and pool["hosts_error"] == "sampler crashed"
    assert pool["medians"] is None and pool["medians_error"] == "db"
    ok = status_json(model(queue=[], recent=[], hosts=[], medians={}))["pools"][0]
    assert ok["queue"] == [] and ok["recent"] == [] and ok["hosts"] == [] and ok["medians"] == {}
    assert ok["queue_error"] is None


def test_paused_and_worker_down_are_visible_in_server():
    wk = [WorkerInfo(lane=1, state="down", error="ENOSPC", since=ago(minutes=1))]
    doc = status_json(
        model(queue=[], paused=Paused(by="macmini-admin", at=ago(minutes=2)), workers=wk)
    )
    assert doc["server"]["paused"] == {"by": "macmini-admin", "at": iso(ago(minutes=2))}
    assert doc["server"]["workers"][0]["state"] == "down"
    assert doc["server"]["workers"][0]["error"] == "ENOSPC"


def test_iso_roundtrip_and_naive_is_utc():
    from datetime import datetime

    assert iso(datetime(2026, 9, 4, 0, 52, 12, 999)) == "2026-09-04T00:52:12Z"
    assert parse_iso("2026-09-04T00:52:12Z") == NOW
    assert iso(None) is None and parse_iso(None) is None


def test_queued_job_without_started_has_null_numbers():
    doc = status_json(
        model(
            queue=compute_queue(
                [job(5, state=QUEUED, created_min=1)],
                workers=default_workers([]),
                paused=True,
                medians={},
                presets=PRESETS,
                cfg=CFG,
                now=NOW,
            )
        )
    )
    est = doc["pools"][0]["queue"][0]["estimate"]
    assert (
        est["elapsed_seconds"] is None and est["wait_seconds"] is None and est["finish_at"] is None
    )
    assert est["source"] == "preset" and est["expected_seconds"] == 480
