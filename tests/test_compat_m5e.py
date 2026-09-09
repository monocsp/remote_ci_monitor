"""호환과 정직성(M5e §10 · §15.6) — `artifacts` 는 **더하기만** 하고, 모르는 것은 `—` 다.

명세는 docs/m5e-workplan.md §10(API 문서 — 추가만) · §3(상태 값) · §9(옛 잡) · §15.6.
**구현보다 먼저 썼다(test-first) — 빨간 것이 정상이다.**

- 키 집합은 `tests/test_status_schema.py` 의 `ROW_KEYS`·`RECENT_KEYS` 를 **가져와** 비교한다.
  나중에 그 목록에 `artifacts` 가 더해져도 이 시험은 그대로 통과한다(합집합이라서) — 그러니 이
  시험이 잠그는 것은 「`artifacts` 말고는 아무 키도 늘거나 이름이 바뀌지 않았다」다.
- 시간 필드는 주입 시계(`test_worker_api.WorkerServer`)로 본다. 수집·업로드에 20초를 태워도
  `finished_at` 과 `job_seconds` 가 밀리면 안 된다.
- 화면의 `—` 는 `core/render_text.DASH` 다(test_render_m5 와 같은 방식 — v1 문서에 키를 얹는다).
"""

from __future__ import annotations

import re
from datetime import UTC
from typing import Any

import pytest

from jobfactory import job as make_job
from remote_ci_monitor import SCHEMA_VERSION
from remote_ci_monitor.config import parse_preset
from remote_ci_monitor.core.model import SUCCEEDED
from remote_ci_monitor.core.render_text import DASH, render
from remote_ci_monitor.core.status import iso, recent_json
from test_render_m5 import doc as v1_doc
from test_server import sh
from test_status_schema import RECENT_KEYS, ROW_KEYS
from test_worker_api import WORKER_PRESETS, WorkerServer

WORKER = "build-02"

#: §10 의 `artifacts` 객체 — 공개 문서에는 **집계만**. 파일 목록·경로·해시는 보호 라우트에만.
ARTIFACT_KEYS = {
    "state",
    "file_count",
    "total_bytes",
    "bundle_bytes",
    "skipped_count",
    "ready_at",
    "expires_at",
    "purged_at",
    "reason_code",
    "reason_args",
}

#: 오늘의 `server` 블록 키(`core/status.py::server_json`). M5e 는 여기에 하나만 더한다.
SERVER_KEYS_V1 = {
    "version",
    "uptime_seconds",
    "lanes",
    "paused",
    "last_error",
    "last_error_code",
    "sse_connections",
    "snapshot_cache",
    "notify_failures",
    "workers",
}

READY = {
    "state": "ready",
    "bundle_sha256": "ab" * 32,
    "file_count": 2,
    "total_bytes": 6,
    "skipped_count": 0,
    "reason_code": None,
    "reason_args": None,
}


# ── 픽스처 · 도우미 ─────────────────────────────────────────────────────────


@pytest.fixture
def srv(tmp_path):
    """`/worker/*` 진짜 서버 + 주입 시계. `gold` 는 글롭이 있고 `ok` 는 없다."""
    s = WorkerServer(tmp_path)
    try:
        gold = sh("gold", "echo gold", artifacts=["out/*.txt"])
        s.cfg.presets = tuple(parse_preset(p) for p in [*WORKER_PRESETS, gold])
    except BaseException:
        s.close()
        raise
    yield s
    s.close()


def claimed_job(srv: WorkerServer, preset: str = "gold") -> int:
    jid = srv.queued_job(preset=preset)
    srv.registered(WORKER)
    assert srv.claimed(WORKER) == jid
    return jid


def finish(srv: WorkerServer, jid: int, **extra: Any):
    body: dict[str, Any] = {"outcome": SUCCEEDED, "exit_code": 0, **extra}
    status, resp = srv.req("POST", f"/worker/jobs/{jid}/finish", token=WORKER, json_body=body)
    assert status == 200, (status, resp)
    return resp


def recent_row(srv: WorkerServer, jid: int) -> dict[str, Any]:
    for pool in srv.status()["pools"]:
        for row in pool["recent"] or []:
            if row["id"] == jid:
                return row
    raise AssertionError(f"job {jid} is not in any recent list")


#: 「모았는데 0개였다」를 사람 말로 그린 모양. 낱말 자리(앞·뒤)는 잠그지 않는다.
ZERO_COUNT_RE = re.compile(r"0\s*(files?|MB|bytes)|(files?|MB|bytes)\s*0")


def recent_block(out: str) -> str:
    """`recent — …` 절만 잘라 낸다 — 산출물 줄이 행 안쪽이든 아래든 이 안에 있다(§13)."""
    lines = out.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.startswith("recent")), None)
    assert start is not None, out
    end = next(
        (
            i
            for i, ln in enumerate(lines[start + 1 :], start=start + 1)
            if ln.startswith(("medians", "host", "queue"))
        ),
        len(lines),
    )
    return "\n".join(lines[start:end])


# ── 추가만 — 기존 v1 키는 이름도 뜻도 그대로 (§10) ─────────────────────────


def test_a_queue_row_gains_only_the_artifacts_object(srv):
    """큐 행의 키는 오늘 그대로 + `artifacts` 하나. 이름이 바뀐 키도 사라진 키도 없다."""
    jid = srv.queued_job(preset="gold")
    row = srv.row(jid)
    assert set(row) == ROW_KEYS | {"artifacts"}, sorted(set(row) ^ (ROW_KEYS | {"artifacts"}))


def test_a_recent_row_gains_only_the_artifacts_object(srv):
    jid = claimed_job(srv)
    finish(srv, jid, artifacts=READY)
    row = recent_row(srv, jid)
    assert set(row) == RECENT_KEYS | {"artifacts"}, sorted(set(row) ^ (RECENT_KEYS | {"artifacts"}))


def test_the_artifacts_object_has_exactly_the_documented_keys(srv):
    """§10: 집계만 — 파일 목록도 경로도 해시도 공개 문서에는 없다."""
    jid = claimed_job(srv)
    finish(srv, jid, artifacts=READY)
    art = recent_row(srv, jid)["artifacts"]
    assert set(art) == ARTIFACT_KEYS, sorted(set(art) ^ ARTIFACT_KEYS)
    assert art["state"] == "ready" and art["file_count"] == 2, art
    assert "files" not in art and "bundle_sha256" not in art and "detail" not in art, art


def test_the_server_block_gains_only_artifact_storage(srv):
    """§10: `server.artifact_storage` 하나만 더한다."""
    server = srv.status()["server"]
    assert set(server) == SERVER_KEYS_V1 | {"artifact_storage"}, sorted(
        set(server) ^ (SERVER_KEYS_V1 | {"artifact_storage"})
    )
    storage = server["artifact_storage"]
    assert set(storage) == {
        "stored_bytes",
        "reserved_bytes",
        "limit_bytes",
        "last_sweep_at",
        "error_code",
    }, storage


def test_the_schema_version_is_still_one(srv):
    """스키마 v1 그대로 — 키는 더하기만 한다(완료 기준 ⑨)."""
    assert SCHEMA_VERSION == 1
    assert srv.status()["schema_version"] == 1


# ── 모르면 unknown, 없으면 disabled — 절대 empty 가 아니다 (§3 · §9) ───────


def test_a_preset_without_globs_is_disabled_not_empty(srv):
    """§3: `disabled` 은 「프리셋에 `artifacts` 가 없다」다. 「모았는데 0개」가 아니다."""
    jid = claimed_job(srv, preset="ok")
    finish(srv, jid)
    art = srv.view(jid)["artifacts"]
    assert art["state"] == "disabled", art
    assert art["file_count"] is None and art["total_bytes"] is None, art


def test_a_job_that_predates_the_feature_reads_as_unknown_and_is_not_collected(srv):
    """§9: M5e 이전 잡은 `job_artifacts` 행이 아예 없다(6→7 마이그레이션이 만들지 않는다).
    그것은 `unknown` 이지 `empty` 가 아니고, 새로 수집하지도 않는다."""
    jid = claimed_job(srv)
    finish(srv, jid, artifacts=READY)
    # 마이그레이션 직후의 모양을 만든다 — 행을 지운다.
    srv.store._conn().execute("DELETE FROM job_artifacts WHERE job_id=?", (jid,))
    srv.store._conn().commit()
    art = srv.view(jid)["artifacts"]
    assert art["state"] == "unknown", art
    assert art["file_count"] is None and art["total_bytes"] is None, art
    assert art["reason_code"] is None, art


def test_the_m3_artifacts_purged_at_is_a_different_thing(srv):
    """§1: `jobs.artifacts_purged_at`(M3 — 로그·스냅샷·워크스페이스)와 `job_artifacts.purged_at`
    은 다른 것이다. M3 청소가 묶음을 지운 것처럼 보이면 안 된다."""
    jid = claimed_job(srv)
    finish(srv, jid, artifacts=READY)
    assert srv.store.mark_artifacts_purged([jid], srv.clock.now) == 1
    art = srv.view(jid)["artifacts"]
    assert art["state"] == "ready", art
    assert art["purged_at"] is None, art


# ── 시간 필드가 밀리지 않는다 (§10 · core/status.py:188) ───────────────────


def test_a_remote_finish_reports_the_execution_end_not_the_upload_end(srv):
    """§10: 수집·업로드가 끼면 서버 수신 시각(`remote_workers.py:448`)이 밀린다 →
    원격 워커가 **실행 종료 시각**을 실어 보내고 서버는 그것을 쓴다."""
    jid = claimed_job(srv)
    started = srv.store.get_job(jid).started_at
    exec_end = srv.clock.advance(30)  # 실행 30초
    srv.clock.advance(20)  # 수집 + 업로드 20초
    finish(srv, jid, finished_at=iso(exec_end), artifacts=READY)
    row = recent_row(srv, jid)
    assert row["started_at"] == iso(started), row
    assert row["finished_at"] == iso(exec_end), row


def test_job_seconds_is_not_inflated_by_collection_and_upload(srv):
    """`job_seconds = finished_at − started_at`(core/status.py:188) 이 50 이 되면 안 된다."""
    jid = claimed_job(srv)
    exec_end = srv.clock.advance(30)
    srv.clock.advance(20)
    finish(srv, jid, finished_at=iso(exec_end), artifacts=READY)
    row = recent_row(srv, jid)
    assert row["job_seconds"] == 30, row
    art = row["artifacts"]
    assert art["state"] == "ready", art
    assert art["ready_at"] is not None, art  # 산출물 시간은 따로 본다


# ── 모르는 값은 — (§10 · §15.6) ───────────────────────────────────────────


def artifact_doc(**over: Any) -> dict[str, Any]:
    base = dict.fromkeys(ARTIFACT_KEYS)
    base["state"] = "unknown"
    base.update(over)
    return base


def doc_with_artifacts(art: dict[str, Any]) -> dict[str, Any]:
    """v1 상태 문서의 **최근 결과 행**에 `artifacts` 를 얹는다(test_render_m5 의 요령 · §13)."""
    d = v1_doc()
    row = recent_json(make_job(400, state=SUCCEEDED, created_min=5, started_min=4, finished_min=3))
    row["artifacts"] = art
    d["pools"][0]["recent"] = [row]
    return d


def test_unknown_artifact_numbers_render_as_a_dash():
    """모르는 수는 `null` 이고 화면은 `—` 로 그린다 — `0` 은 「모았는데 없었다」일 때만이다."""
    block = recent_block(render(doc_with_artifacts(artifact_doc()), tz=UTC))
    assert DASH in block, block
    assert ZERO_COUNT_RE.search(block) is None, block


def test_a_collection_that_found_nothing_renders_zero_not_a_dash():
    """`empty` 는 아는 사실이다 — 0 으로 그린다. `unknown` 과 화면에서도 구분된다."""
    art = artifact_doc(state="empty", file_count=0, total_bytes=0, bundle_bytes=0, skipped_count=0)
    block = recent_block(render(doc_with_artifacts(art), tz=UTC))
    assert ZERO_COUNT_RE.search(block) is not None, block
    unknown = recent_block(render(doc_with_artifacts(artifact_doc()), tz=UTC))
    assert block != unknown, (block, unknown)
