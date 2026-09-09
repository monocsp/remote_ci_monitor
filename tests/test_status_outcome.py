"""상태 문서가 코드를 함께 내려보낸다(M5d-0, 오너 결정 37) — 스키마 v1 은 **그대로**고 키만 는다.

`summary` 옆에 `summary_code`·`summary_args` 가, `*_error` 옆에 `*_error_code` 가, `gpu_note` 옆에
`gpu_note_code` 가 붙는다. 잡이 `::rcm::summary::` 로 스스로 찍은 요약에는 코드가 없다 — 팀이 쓴
문장이라 그대로 보여 준다.

기준선(`BASE_*`)은 **이 변경 직전(2026-09-08)의 키 집합을 그대로 옮겨 적은 것**이다. 다른 테스트
파일에서 가져오지 않고 여기 박아 두는 이유는, 그 파일이 같이 바뀌면 「키가 사라졌다」를 못 잡기
때문이다. 기준선이 부분집합인지 먼저 보고(지워진 키 없음), 늘어난 키가 예정된 것뿐인지 본다.
명세는 docs/m5d-workplan.md §4.5. 구현 전이라 빨간 것이 정상이다.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import pytest

from jobfactory import CFG, GATE, MEDIANS, NOW, PRESETS, QA, ago, default_workers, job
from remote_ci_monitor import SCHEMA_VERSION
from remote_ci_monitor.core.model import (
    CANCELLED,
    FAILED,
    RUNNING,
    SUCCEEDED,
    HostSample,
    Job,
    Pool,
    QueueRow,
    ServerInfo,
    StatusModel,
    Transition,
)
from remote_ci_monitor.core.queue import compute_queue
from remote_ci_monitor.core.status import host_json, recent_json, status_json

# ── 이 변경 직전의 키 집합 ────────────────────────────────────────────────────

BASE_TOP = frozenset(
    {"schema_version", "generated_at", "display_timezone", "server", "presets", "pools"}
)
BASE_SERVER = frozenset(
    {
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
)
BASE_POOL = frozenset(
    {
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
)
BASE_RECENT = frozenset(
    {
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
        "last_step",  # M5h — 「끝났을 때 어디였나」
        "cancelled_by",
        "timeout_seconds",
        "source",
        "transitions",
        "url",
    }
)
BASE_ROW = frozenset(
    {
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
)
BASE_HOST = frozenset(
    {
        "name",
        "source",
        "sampled_at",
        "age_seconds",
        "stale",
        "interval_seconds",
        "os",
        "cores",
        "load",
        "cpu",
        "memory",
        "gpu",
        "gpu_note",
        "top",
        "history",
    }
)

#: M5d-0 이 더해도 되는 키. 이 밖의 키가 늘면 스키마 v1 약속을 넘은 것이다.
NEW_SERVER = frozenset({"last_error_code"})
NEW_POOL = frozenset(
    {"queue_error_code", "recent_error_code", "medians_error_code", "hosts_error_code"}
)
NEW_RECENT = frozenset({"summary_code", "summary_args"})
NEW_ROW = frozenset({"summary", "summary_code", "summary_args"})
NEW_HOST = frozenset({"gpu_note_code", "disk"})  # `disk` 는 M5d-2 §4.6 (가)


# ── 조립 도우미 ───────────────────────────────────────────────────────────────


#: 「인자를 안 줬다」와 「None 을 줬다(= 조회 실패)」를 가르는 표식
_KEEP = object()


def make_pool(
    *,
    queue: Sequence[QueueRow] | None = (),
    recent: Sequence[Job] | None = (),
    hosts: Sequence[HostSample] | None = (),
    medians: dict[str, Any] | None | object = _KEEP,
    **extra: Any,
) -> Pool:
    """`extra` 로 `*_error`/`*_error_code` 를 넣는다 — 없는 인자면 여기서 TypeError 가 난다."""
    fields: dict[str, Any] = {
        "queue_error": None,
        "recent_error": None,
        "medians_error": None,
        "hosts_error": None,
    }
    fields.update(extra)
    return Pool(
        name="default",
        lanes=1,
        queue=None if queue is None else tuple(queue),
        recent=None if recent is None else tuple(recent),
        recent_count=8,
        medians=MEDIANS if medians is _KEEP else medians,
        hosts=None if hosts is None else tuple(hosts),
        **fields,
    )


def make_model(pool: Pool, **server_extra: Any) -> StatusModel:
    wk = default_workers([412])
    server = ServerInfo(
        version="0.1.0",
        uptime_seconds=8123,
        lanes=len(wk),
        paused=None,
        workers=tuple(wk),
        **{"last_error": None, **server_extra},
    )
    return StatusModel(
        generated_at=NOW,
        display_timezone=None,
        server=server,
        presets=(GATE, QA),
        pools=(pool,),
        base_url="http://macmini:8787",
    )


def coded_job(**kw: Any) -> Job:
    """서버가 닫은 잡 — 문장과 코드와 원시 인자가 함께 온다."""
    fields: dict[str, Any] = dict(
        summary="worker build-02 unreachable for 61s",
        summary_code="worker_unreachable",
        summary_args={"name": "build-02", "seconds": 61},
    )
    fields.update(kw)
    return job(
        411,
        "gate:fast",
        FAILED,
        created_min=9,
        started_min=8,
        finished_min=6,
        exit_code=1,
        failed_step="test",
        timeout_seconds=1200,
        transitions=(Transition("queued", ago(minutes=9)), Transition("failed", ago(minutes=6))),
        **fields,
    )


def self_reported_job() -> Job:
    """잡이 `::rcm::summary::` 로 스스로 찍은 요약 — 코드가 없다."""
    return job(
        410,
        "gate:full",
        SUCCEEDED,
        created_min=20,
        started_min=19,
        finished_min=12,
        exit_code=0,
        summary="3 flaky, 0 failed",
    )


def queue_rows(jobs: Sequence[Job]) -> list[QueueRow]:
    return compute_queue(
        list(jobs),
        workers=default_workers([412]),
        paused=False,
        medians=MEDIANS,
        presets=PRESETS,
        cfg=CFG,
        now=NOW,
    )


def sample_host(**extra: Any) -> HostSample:
    return HostSample(
        name="macmini",
        source="local",
        sampled_at=ago(seconds=4),
        interval_seconds=5,
        os="darwin",
        cores=10,
        load=(3.48, 3.1, 2.9),
        cpu={"user": 17.0, "sys": 4.0, "idle": 79.0, "busy": 21.0},
        memory={"total_bytes": 24 * 10**9, "used_bytes": 14 * 10**9, "compressed_bytes": None},
        **extra,
    )


def full_doc() -> dict[str, Any]:
    """큐·최근·호스트가 모두 찬 문서. 새 필드는 넣지 않는다(오늘 모양 그대로 조립된다)."""
    rows = queue_rows([job(412, state=RUNNING, created_min=2, started_min=1), job(413)])
    pool = make_pool(queue=rows, recent=[coded_job(), self_reported_job()], hosts=[sample_host()])
    return status_json(make_model(pool), log_tails={412: ["[test] 3/9"]})


# ── 스키마 v1 은 그대로다 ─────────────────────────────────────────────────────


def test_schema_version_is_still_one_and_the_document_still_serializes():
    doc = full_doc()
    assert doc["schema_version"] == SCHEMA_VERSION == 1
    assert json.loads(json.dumps(doc))["schema_version"] == 1  # summary_args 가 dict 여도 된다


# ── 키는 더하기만 한다 ────────────────────────────────────────────────────────


def test_no_key_that_exists_today_disappears():
    doc = full_doc()
    assert set(doc) == set(BASE_TOP)  # 최상위는 늘지도 줄지도 않는다
    server = doc["server"]
    assert BASE_SERVER <= set(server)
    assert set(server) - BASE_SERVER <= NEW_SERVER
    pool = doc["pools"][0]
    assert BASE_POOL <= set(pool)
    assert set(pool) - BASE_POOL <= NEW_POOL
    for row in pool["queue"]:
        assert BASE_ROW <= set(row)
        assert set(row) - BASE_ROW <= NEW_ROW
    for entry in pool["recent"]:
        assert BASE_RECENT <= set(entry)
        assert set(entry) - BASE_RECENT <= NEW_RECENT
    for host in pool["hosts"]:
        assert BASE_HOST <= set(host)
        assert set(host) - BASE_HOST <= NEW_HOST


# ── recent[] ──────────────────────────────────────────────────────────────────


def test_recent_entry_carries_the_code_and_the_raw_args():
    entry = recent_json(coded_job(), base_url="http://macmini:8787")
    assert entry["summary"] == "worker build-02 unreachable for 61s"
    assert entry["summary_code"] == "worker_unreachable"
    assert entry["summary_args"] == {"name": "build-02", "seconds": 61}
    assert entry["summary_args"]["seconds"] == 61  # 원시 값 — `61s` 가 아니다


def test_recent_entry_of_a_job_that_wrote_its_own_summary_has_no_code():
    entry = recent_json(self_reported_job())
    assert entry["summary"] == "3 flaky, 0 failed"
    assert entry["summary_code"] is None
    assert entry["summary_args"] is None


def test_recent_entry_without_any_summary_has_neither():
    entry = recent_json(job(9, state=SUCCEEDED, created_min=3, started_min=2, finished_min=1))
    assert entry["summary"] is None
    assert entry["summary_code"] is None and entry["summary_args"] is None


def test_recent_in_the_status_document_carries_the_code():
    pool = full_doc()["pools"][0]
    by_id = {e["id"]: e for e in pool["recent"]}
    assert by_id[411]["summary_code"] == "worker_unreachable"
    assert by_id[411]["summary_args"] == {"name": "build-02", "seconds": 61}
    assert by_id[410]["summary_code"] is None and by_id[410]["summary_args"] is None
    assert by_id[410]["summary"] == "3 flaky, 0 failed"


# ── queue[] — 요약이 있는 경우에만 ────────────────────────────────────────────


def test_queue_entry_never_shows_a_code_without_the_sentence():
    """활성 잡에는 보통 요약이 없다. 실어 보낸다면 문장·코드·인자가 함께여야 한다."""
    running = job(
        412,
        state=RUNNING,
        created_min=2,
        started_min=1,
        summary="cancelled by alice@laptop",
        summary_code="cancelled_by",
        summary_args={"by": "alice@laptop"},
    )
    doc = status_json(make_model(make_pool(queue=queue_rows([running]), recent=[])))
    row = doc["pools"][0]["queue"][0]
    assert BASE_ROW <= set(row)
    assert set(row) - BASE_ROW <= NEW_ROW
    if "summary_code" in row:
        assert {"summary", "summary_args"} <= set(row)  # 코드만 홀로 오지 않는다
        assert row["summary"] == running.summary
        assert row["summary_code"] == "cancelled_by"
        assert row["summary_args"] == {"by": "alice@laptop"}


# ── *_error_code ─────────────────────────────────────────────────────────────


def test_each_failed_section_gains_a_code_beside_its_error_text():
    pool = make_pool(
        queue=None,
        queue_error="database is locked",
        queue_error_code="database_unavailable",
        recent=None,
        recent_error="database is locked",
        recent_error_code="database_unavailable",
        medians=None,
        medians_error="database is locked",
        medians_error_code="database_unavailable",
        hosts=None,
        hosts_error="sampler crashed",
        hosts_error_code="sampler_failed",
    )
    doc = status_json(make_model(pool, last_error="boom", last_error_code="internal_error"))
    p = doc["pools"][0]
    assert p["queue"] is None and p["queue_error"] == "database is locked"
    assert p["queue_error_code"] == "database_unavailable"
    assert p["recent"] is None and p["recent_error_code"] == "database_unavailable"
    assert p["medians"] is None and p["medians_error_code"] == "database_unavailable"
    assert p["hosts"] is None and p["hosts_error"] == "sampler crashed"
    assert p["hosts_error_code"] == "sampler_failed"
    assert doc["server"]["last_error"] == "boom"
    assert doc["server"]["last_error_code"] == "internal_error"


def test_no_error_means_no_code_either():
    doc = full_doc()
    p = doc["pools"][0]
    for name in ("queue", "recent", "medians", "hosts"):
        assert p[f"{name}_error"] is None
        assert p.get(f"{name}_error_code") is None, name
    assert doc["server"]["last_error"] is None
    assert doc["server"].get("last_error_code") is None


# ── gpu_note_code ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("note", "code"),
    [
        ("nvidia-smi not found", "no_sampler"),
        ("disabled", "no_gpu"),
        ("nvidia-smi failed", "sampler_failed"),
    ],
)
def test_gpu_note_code_travels_beside_the_note(note, code):
    host = sample_host(gpu=None, gpu_note=note, gpu_note_code=code)
    doc = host_json(host, now=NOW)
    assert doc["gpu"] is None
    assert doc["gpu_note"] == note  # 원문은 그대로 — 디버깅에 쓴다
    assert doc["gpu_note_code"] == code


def test_a_host_with_a_gpu_has_neither_note_nor_code():
    host = sample_host(gpu={"util_pct": 13, "mem_used_bytes": 594411520, "source": "ioreg"})
    doc = host_json(host, now=NOW)
    assert doc["gpu_note"] is None
    assert doc.get("gpu_note_code") is None


# ── disk (M5d-2 §4.6 (가)) ───────────────────────────────────────────────────

#: 잡이 쓰는 파일 시스템 — 루트 파티션이 아니라 데이터 디렉터리다.
DISK = {
    "used_bytes": 120 * 10**9,
    "free_bytes": 340 * 10**9,
    "total_bytes": 460 * 10**9,
    "path": "/var/lib/rcm",
}


def test_host_carries_the_disk_of_the_filesystem_the_jobs_use():
    host = sample_host(disk=DISK)
    doc = host_json(host, now=NOW)
    assert doc["disk"] == DISK
    assert doc["disk"] is not host.disk  # 사본 — 문서를 고쳐도 표본이 안 바뀐다
    assert doc["disk"]["free_bytes"] == 340 * 10**9  # 원시 바이트 — `340 GB` 가 아니다


def test_a_host_that_could_not_read_its_disk_still_has_the_key():
    doc = host_json(sample_host(), now=NOW)
    assert "disk" in doc and doc["disk"] is None
    assert doc["cpu"] is not None and doc["memory"] is not None  # 나머지 칸은 그대로 산다


def test_disk_survives_the_whole_status_document_and_the_schema_is_still_v1():
    pool = make_pool(hosts=[sample_host(disk=DISK)])
    doc = status_json(make_model(pool))
    (host,) = doc["pools"][0]["hosts"]
    assert host["disk"] == DISK
    assert BASE_HOST <= set(host)
    assert set(host) - BASE_HOST <= NEW_HOST
    assert doc["schema_version"] == SCHEMA_VERSION == 1
    assert json.loads(json.dumps(doc))["pools"][0]["hosts"][0]["disk"] == DISK


# ── 취소된 잡의 문장은 그대로 ────────────────────────────────────────────────


def test_a_cancelled_job_keeps_the_exact_english_sentence_it_has_today():
    """화면이 코드로 다시 그리더라도 CLI·알림 훅이 읽는 `summary` 는 오늘과 같은 글자다."""
    cancelled = job(
        414,
        "gate:full",
        CANCELLED,
        created_min=5,
        finished_min=4,
        cancelled_by="alice-laptop",
        summary="cancelled before start",
        summary_code="cancelled_before_start",
        summary_args={},
    )
    entry = recent_json(cancelled)
    assert entry["summary"] == "cancelled before start"
    assert entry["summary_code"] == "cancelled_before_start"
    assert entry["summary_args"] in ({}, None)  # 인자 없는 코드 — 빈 사전이든 null 이든 좋다
