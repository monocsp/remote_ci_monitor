"""M5s A — 서버의 예약 실행: 제출 · 합류 안 함 · 중복 409 · 업로드 세 길 · 승격과 만료 · 상태 문서
· health(`docs/m5s-workplan.md` §4.1 · §7 S1 S2 S4 S6 S8 S19 S28).

서버는 `test_worker_api.WorkerServer`(주입 시계 · 로컬 워커 스레드 없음)다. 5초 주기 루프 대신
`App.tick_schedule()` 을 시험이 직접 부른다 — 루프는 그 함수를 부를 뿐이다.
"""

from __future__ import annotations

import itertools
from datetime import timedelta
from typing import Any

import pytest

from remote_ci_monitor import server as rcm_server
from remote_ci_monitor.config import parse_preset
from remote_ci_monitor.core.model import (
    CANCELLED,
    EXPIRED,
    QUEUED,
    RUNNING,
    SCHEDULED,
    UPLOADING,
)
from remote_ci_monitor.core.status import iso
from test_server import TAR, sh
from test_server_m5 import BLOBS, blobs_for, manifest_of
from test_worker_api import GIT_SHA, T0, WORKER_PRESETS, WorkerServer

_trees = (f"{i:064x}" for i in itertools.count(1))
FILES = {"hello.txt": b"hello\n"}


@pytest.fixture
def srv(tmp_path):
    s = WorkerServer(tmp_path)
    s.cfg.display.timezone = "Asia/Seoul"  # T0 = 12:00Z = 서울 21:00
    s.cfg.presets = (
        *s.cfg.presets,
        parse_preset(
            sh(
                "short",
                "echo short",
                source_modes=["git_ref"],
                repo="app",
                start_deadline_seconds=60,
            )
        ),
    )
    yield s
    s.close()


def git(srv, token: str = "alice", *, preset: str = "deploy", **extra: Any):
    body = {
        "preset": preset,
        "inputs": {},
        "source": {"mode": "git_ref", "ref": GIT_SHA},
        "requester_label": f"{token}@host",
        **extra,
    }
    return srv.req("POST", "/jobs", token=token, json_body=body)


def tree(srv, token: str = "alice", *, tree_hash: str | None = None, **extra: Any):
    body = {
        "preset": "ok",
        "inputs": {},
        "source": {
            "mode": "tree",
            "repo": "org/app",
            "branch": "feat/x",
            "tree_hash": tree_hash or next(_trees),
            "bytes": len(TAR),
        },
        "requester_label": f"{token}@host",
        **extra,
    }
    return srv.req("POST", "/jobs", token=token, json_body=body)


def state(srv, jid: int) -> str:
    return srv.store.get_job(jid).state


# ── 제출 ────────────────────────────────────────────────────────────────────


def test_a_git_ref_submission_with_seconds_is_scheduled(srv):
    status, resp = git(srv, start_in_seconds=300)
    assert status == 201, resp
    assert resp["state"] == SCHEDULED and resp["joined"] is False
    assert resp["not_before"] == iso(T0 + timedelta(seconds=300))
    assert resp["start_deadline"] == iso(T0 + timedelta(seconds=1200))  # 기본 900초
    assert resp["timezone"] == "Asia/Seoul"
    assert resp["submission"]["cancel_token"]
    j = srv.store.get_job(resp["job_id"])
    assert j.state == SCHEDULED and j.queued_at is None


def test_start_at_without_offset_is_read_in_the_display_timezone(srv):
    status, resp = git(srv, start_at="22:30")  # 서울 22:30 = 13:30Z
    assert status == 201, resp
    assert resp["not_before"] == "2026-09-06T13:30:00Z"


def test_the_server_local_zone_is_used_when_display_timezone_is_empty(srv, monkeypatch):
    srv.cfg.display.timezone = ""
    monkeypatch.setenv("TZ", "UTC")
    status, resp = git(srv, start_at="12:30")
    assert status == 201, resp
    assert resp["not_before"] == "2026-09-06T12:30:00Z" and resp["timezone"] == "UTC"


@pytest.mark.parametrize(
    ("extra", "code"),
    [
        ({"start_at": "20:00"}, "start_at_past"),
        ({"start_at": "soon"}, "start_at_invalid"),
        ({"start_at": "22:00", "start_in_seconds": 60}, "start_at_conflict"),
        ({"start_in_seconds": 15 * 86400}, "start_at_too_far"),
        ({"start_in_seconds": 0}, "start_in_invalid"),
    ],
)
def test_bad_start_times_are_400_with_a_code_and_create_nothing(srv, extra, code):
    status, resp = git(srv, **extra)
    assert status == 400, resp
    assert resp["code"] == code and resp["error_code"] == code
    assert srv.store.list_scheduled() == [] and srv.store.list_active() == []


def test_a_submission_can_shorten_the_deadline_but_not_lengthen_it(srv):
    status, resp = git(srv, start_in_seconds=60, start_deadline_seconds=30)
    assert status == 201, resp
    assert resp["start_deadline"] == iso(T0 + timedelta(seconds=90))
    status, resp = git(srv, start_in_seconds=60, start_deadline_seconds=901)
    assert status == 400 and resp["code"] == "start_deadline_too_long", resp


def test_the_presets_deadline_overrides_the_server_default(srv):
    status, resp = git(srv, preset="short", start_in_seconds=60)
    assert status == 201, resp
    assert resp["start_deadline"] == iso(T0 + timedelta(seconds=120))


def test_a_deadline_without_a_start_time_is_400(srv):
    status, resp = git(srv, start_deadline_seconds=30)
    assert status == 400 and "start_deadline_seconds" in resp["error"], resp


# ── 합류 안 함 (결정 226) ───────────────────────────────────────────────────


def test_s19_a_schedule_does_not_join_an_active_identical_job(srv):
    s1, now = git(srv)
    assert s1 == 201 and now["state"] == QUEUED
    s2, later = git(srv, start_in_seconds=300)
    assert s2 == 201, later
    assert later["joined"] is False and later["job_id"] != now["job_id"]
    assert later["state"] == SCHEDULED
    assert srv.store.get_job(now["job_id"]).joiners == ()


def test_s8_an_immediate_run_does_not_join_a_schedule(srv):
    s1, sched = git(srv, start_in_seconds=300)
    s2, now = git(srv, "bob")
    assert s1 == 201 and s2 == 201, (sched, now)
    assert now["joined"] is False and now["job_id"] != sched["job_id"]
    assert now["state"] == QUEUED
    assert srv.store.get_job(sched["job_id"]).joiners == ()


def test_s6_the_same_schedule_twice_is_409_schedule_exists(srv):
    s1, first = git(srv, start_in_seconds=300)
    s2, second = git(srv, "bob", start_in_seconds=600)
    assert s1 == 201 and s2 == 409, second
    assert second["code"] == "schedule_exists" and second["job_id"] == first["job_id"]
    assert len(srv.store.list_scheduled()) == 1


# ── 업로드 세 길 (S28) ──────────────────────────────────────────────────────


def test_s28_a_full_tar_upload_of_a_schedule_answers_scheduled(srv):
    status, resp = tree(srv, start_in_seconds=300)
    assert status == 201 and resp["state"] == UPLOADING, resp
    jid = resp["job_id"]
    status, up = srv.req("PUT", f"/jobs/{jid}/tree", token="alice", body=TAR)
    assert status == 200 and up["state"] == SCHEDULED, up
    assert state(srv, jid) == SCHEDULED


def test_s28_manifest_then_blobs_of_a_schedule_answers_scheduled(srv):
    status, resp = tree(srv, start_in_seconds=300)
    jid = resp["job_id"]
    status, m = srv.req(
        "POST", f"/jobs/{jid}/tree/manifest", token="alice", json_body=manifest_of(FILES)
    )
    assert status == 200 and m["state"] == UPLOADING and m["missing"], m
    status, up = srv.req(
        "PUT",
        f"/jobs/{jid}/tree",
        token="alice",
        body=blobs_for(FILES, m["missing"]),
        headers=BLOBS,
    )
    assert status == 200 and up["state"] == SCHEDULED, up
    assert state(srv, jid) == SCHEDULED


def test_s28_a_manifest_with_nothing_missing_answers_scheduled(srv):
    _s, first = tree(srv)
    fid = first["job_id"]
    _s, m = srv.req(
        "POST", f"/jobs/{fid}/tree/manifest", token="alice", json_body=manifest_of(FILES)
    )
    srv.req(
        "PUT",
        f"/jobs/{fid}/tree",
        token="alice",
        body=blobs_for(FILES, m["missing"]),
        headers=BLOBS,
    )
    _s, resp = tree(srv, start_in_seconds=300)
    jid = resp["job_id"]
    status, m2 = srv.req(
        "POST", f"/jobs/{jid}/tree/manifest", token="alice", json_body=manifest_of(FILES)
    )
    assert status == 200 and m2["missing"] == [] and m2["state"] == SCHEDULED, m2
    assert state(srv, jid) == SCHEDULED


def test_an_immediate_tree_upload_still_answers_queued(srv):
    _s, resp = tree(srv)
    status, up = srv.req("PUT", f"/jobs/{resp['job_id']}/tree", token="alice", body=TAR)
    assert status == 200 and up["state"] == QUEUED, up


# ── 승격 · 만료 (S1 · S4 단위) ──────────────────────────────────────────────


def test_s1_the_tick_promotes_at_the_time_and_not_before(srv):
    _s, resp = git(srv, start_in_seconds=300)
    jid = resp["job_id"]
    srv.clock.advance(299)
    srv.app.tick_schedule()
    assert state(srv, jid) == SCHEDULED
    srv.clock.advance(1)
    srv.app.tick_schedule()
    assert state(srv, jid) == QUEUED
    assert srv.store.get_job(jid).queued_at == T0 + timedelta(seconds=300)


def test_s4_a_promoted_job_no_lane_takes_expires_at_the_deadline(srv):
    _s, resp = git(srv, start_in_seconds=60)
    jid = resp["job_id"]
    srv.clock.advance(60)
    srv.app.tick_schedule()
    srv.clock.advance(900)
    srv.app.tick_schedule()
    assert state(srv, jid) == QUEUED  # 마감 그 자체는 아직 안
    srv.clock.advance(1)
    srv.app.tick_schedule()
    assert state(srv, jid) == EXPIRED
    _s, view = srv.req("GET", f"/jobs/{jid}", token="alice")
    assert view["state"] == EXPIRED and view["summary_code"] == "start_deadline_passed"


def test_a_remote_worker_does_not_claim_a_job_past_its_deadline(srv):
    _s, resp = git(srv, start_in_seconds=60)
    jid = resp["job_id"]
    srv.clock.advance(60)
    srv.app.tick_schedule()
    srv.clock.advance(1000)  # 마감을 넘겼지만 주기 판정은 아직 안 돌았다
    srv.registered("build-02")
    status, _body = srv.claim("build-02")
    assert status == 204
    assert state(srv, jid) == QUEUED


def test_s2_cancelling_a_schedule_over_http(srv):
    _s, resp = git(srv, start_in_seconds=300)
    jid = resp["job_id"]
    status, out = srv.req("POST", f"/jobs/{jid}/cancel", token="alice", json_body={})
    assert status == 200 and out["state"] == CANCELLED, out
    srv.clock.advance(400)
    srv.app.tick_schedule()
    assert state(srv, jid) == CANCELLED


def test_bump_reaches_a_schedule(srv):
    _s, resp = git(srv, start_in_seconds=300)
    status, out = srv.req(
        "POST", f"/jobs/{resp['job_id']}/priority", token="admin", json_body={"priority": "high"}
    )
    assert status == 200, out
    assert srv.store.get_job(resp["job_id"]).priority == 1


# ── supersede 끄기는 DB 에 얼린다 (결정 237) ────────────────────────────────


@pytest.fixture
def shas(monkeypatch):
    counter = itertools.count(1)
    monkeypatch.setattr(
        rcm_server,
        "resolve_ref",
        lambda url, ref, *, timeout, credential=None: f"{next(counter):040x}",
    )


def use_supersede(srv) -> None:
    srv.cfg.presets = (
        *(parse_preset(p) for p in WORKER_PRESETS),
        parse_preset(sh("d", "echo d", supersede="all", source_modes=["git_ref"], repo="app")),
    )


def ref_job(srv, **extra: Any):
    body = {
        "preset": "d",
        "inputs": {},
        "source": {"mode": "git_ref", "ref": "feat/x"},
        "requester_label": "alice@host",
        **extra,
    }
    return srv.req("POST", "/jobs", token="alice", json_body=body)


def test_a_schedule_is_not_superseded_while_it_waits(srv, shas):
    use_supersede(srv)
    _s, sched = ref_job(srv, start_in_seconds=60)
    _s, newer = ref_job(srv)
    assert state(srv, sched["job_id"]) == SCHEDULED  # 예약은 supersede 대상이 아니다
    assert state(srv, newer["job_id"]) == QUEUED


def test_a_promoted_schedule_supersedes_the_older_job_of_its_branch(srv, shas):
    use_supersede(srv)
    _s, old = ref_job(srv)
    _s, sched = ref_job(srv, start_in_seconds=60)
    assert state(srv, old["job_id"]) == QUEUED  # 예약을 걸 때는 아무것도 치우지 않는다
    srv.clock.advance(60)
    srv.app.tick_schedule()
    assert state(srv, old["job_id"]) == CANCELLED  # 승격이 큐에 들어온 순간이다(결정 111)
    assert state(srv, sched["job_id"]) == QUEUED


def test_no_supersede_is_frozen_on_the_row_and_survives_a_restart(srv, shas, tmp_path):
    use_supersede(srv)
    _s, old = ref_job(srv)
    _s, sched = ref_job(srv, start_in_seconds=60, supersede=False)
    assert srv.store.get_job(sched["job_id"]).no_supersede is True
    srv.app._no_supersede.clear()  # 재시작하면 메모리의 표시는 사라진다
    srv.clock.advance(60)
    srv.app.tick_schedule()
    assert state(srv, old["job_id"]) == QUEUED
    assert state(srv, sched["job_id"]) == QUEUED


# ── 문서 ────────────────────────────────────────────────────────────────────


def test_status_lists_schedules_apart_from_the_queue(srv):
    _s, resp = git(srv, start_in_seconds=300)
    _s, doc = srv.req("GET", "/api/status")
    pool = doc["pools"][0]
    assert [r["id"] for r in pool["queue"]] == []
    rows = pool["scheduled"]
    assert [r["id"] for r in rows] == [resp["job_id"]]
    row = rows[0]
    assert row["state"] == SCHEDULED
    assert row["not_before"] == iso(T0 + timedelta(seconds=300))
    assert row["start_deadline"] == iso(T0 + timedelta(seconds=1200))
    assert pool["scheduled_error"] is None and pool["scheduled_error_code"] is None


def test_status_orders_schedules_by_time(srv):
    _s, late = git(srv, start_in_seconds=900)
    _s, soon = git(srv, preset="short", start_in_seconds=60)
    _s, doc = srv.req("GET", "/api/status")
    assert [r["id"] for r in doc["pools"][0]["scheduled"]] == [soon["job_id"], late["job_id"]]


def test_a_queue_row_carries_the_schedule_it_came_from(srv):
    _s, resp = git(srv, start_in_seconds=60)
    srv.clock.advance(60)
    srv.app.tick_schedule()
    _s, doc = srv.req("GET", "/api/status")
    row = next(r for r in doc["pools"][0]["queue"] if r["id"] == resp["job_id"])
    assert row["not_before"] == iso(T0 + timedelta(seconds=60))
    assert row["start_deadline"] == iso(T0 + timedelta(seconds=960))


def test_job_view_of_a_schedule_says_so(srv):
    _s, resp = git(srv, start_in_seconds=300)
    status, view = srv.req("GET", f"/jobs/{resp['job_id']}", token="alice")
    assert status == 200
    assert view["state"] == SCHEDULED and view["id"] == resp["job_id"]
    assert view["not_before"] == iso(T0 + timedelta(seconds=300))
    assert view["finished_at"] is None


def test_health_announces_scheduling(srv):
    _s, h = srv.req("GET", "/api/health")
    assert h["schedule"] is True


def test_a_running_job_is_left_alone_by_the_deadline(srv):
    _s, resp = git(srv, start_in_seconds=60)
    jid = resp["job_id"]
    srv.clock.advance(60)
    srv.app.tick_schedule()
    assert srv.store.claim(1, srv.clock()) is not None
    srv.clock.advance(5000)
    srv.app.tick_schedule()
    assert state(srv, jid) == RUNNING
