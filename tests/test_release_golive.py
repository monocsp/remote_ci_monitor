"""`POST …/release/golive` — 승인된 빌드를 사람이 요청한 때에 출시하는 선택 역할의 문(M5s §4.5).

명세: docs/m5s-workplan.md §4.5 · 결정 215 · 216 · 224 · 232 · 235 · §7 S14 · S21 · S23 · S24.

여기서 지키는 것:
- plan 모드는 아무 클라이언트 토큰으로 받는다. release 모드는 admin 이어야 한다
  (403 `admin_required`).
- release 의 409 는 서버가 지킨다. 같은 build_name 의 성공한 golive plan 이 없으면
  `golive_plan_required`, 낡았으면 `golive_plan_stale`(거는 순간에만 본다), 고른 플랫폼이 승인
  상태가 아니면 `golive_plan_blocked`(고른 플랫폼의 `approved` 만 본다), 친 번호가 `approved_build`
  와 다르면 `build_number_mismatch`(`auto` 는 받지 않고, both 면 두 플랫폼 모두 같아야 한다), 같은
  build_name 의 출시 사슬이 예약이거나 살아 있으면 `golive_running`.
- plan 모드에 시각이 오면 400 이다.
- 제출된 초안에서도 받는다. 버린 초안은 그대로 409 `version_closed` 다.
- `POST /jobs` 로 golive 역할 프리셋의 `mode = release` 를 내면 누구든 409
  `release_route_required` 다. review·upload 는 그대로 통과한다(드라이버가 `rcm run` 으로 upload 를
  낸다).
"""

from __future__ import annotations

import json
import threading
from datetime import timedelta
from pathlib import Path

import pytest

from remote_ci_monitor.config import RepoConfig, parse_preset, parse_release_profile
from remote_ci_monitor.core.model import QUEUED, SCHEDULED
from test_server_release_routes import (
    BUILD,
    CHOICE,
    CONFIRM,
    NOW,
    PLATFORM,
    PROFILE,
    ReleaseServer,
    preset,
)
from test_server_release_routes import remote as _remote

remote = _remote  # 픽스처를 이 모듈의 이름공간에

GOLIVE = "release-golive"
GOLIVE_INPUTS = [
    BUILD,
    {"name": "build_name_android", "type": "string", "default": ""},
    CONFIRM,
    {"name": "mode", "type": CHOICE, "choices": ["plan", "release"], "default": "plan"},
    PLATFORM,
]
IOS_APPROVED = {"approved": True, "approved_build": 185, "raw_state": "PENDING_DEVELOPER_RELEASE"}
ANDROID_IN_REVIEW = {
    "approved": False,
    "approved_build": None,
    "raw_state": "RELEASE_LIFECYCLE_STATE_IN_REVIEW",
}
ANDROID_APPROVED = {
    "approved": True,
    "approved_build": 185,
    "raw_state": "RELEASE_LIFECYCLE_STATE_APPROVED_NOT_PUBLISHED",
}
#: 2026-11-23 실측 모양 — iOS 승인 대기, Android 심사 중(S21)
GOLIVE_PLAN = {
    "schema": 1,
    "build_name": "1.0.1",
    "measured_at": "2026-09-17T11:56:00Z",
    "plan_verdict": "blocked",
    "reasons": ["android_not_approved"],
    "platforms": {"ios": IOS_APPROVED, "android": ANDROID_IN_REVIEW},
}
BOTH_APPROVED = {
    **GOLIVE_PLAN,
    "plan_verdict": "ok",
    "reasons": [],
    "platforms": {"ios": IOS_APPROVED, "android": ANDROID_APPROVED},
}


class GoliveServer(ReleaseServer):
    """`golive` 역할 프리셋이 있는 `app` — 나머지는 스토어 탭 라우트 시험과 같다."""

    def __init__(self, tmp_path: Path, remote, **overrides):
        super().__init__(tmp_path, remote, **overrides)
        profile = {**PROFILE, "presets": {**PROFILE["presets"], "golive": GOLIVE}}
        self.cfg.repos = (
            RepoConfig(name="app", url=remote.url, release=parse_release_profile("app", profile)),
        )
        self.cfg.presets = (parse_preset(preset(GOLIVE, GOLIVE_INPUTS)), *self.cfg.presets)

    def golive_plan_job(self, doc: dict = GOLIVE_PLAN, **kw) -> int:
        return self.role_job(GOLIVE, {"golive-plan.json": json.dumps(doc).encode()}, **kw)

    def golive(self, body: dict, token: str = "admin"):
        return self.post("golive", body, token=token)

    def post_jobs(self, preset_name: str, inputs: dict, token: str = "alice"):
        return self.req(
            "POST",
            "/jobs",
            token=token,
            json_body={
                "preset": preset_name,
                "inputs": inputs,
                "source": {"mode": "git_ref", "ref": "main"},
            },
        )


@pytest.fixture
def gsrv(tmp_path: Path, remote, monkeypatch):
    s = GoliveServer(tmp_path, remote)
    try:
        s.open_gate(monkeypatch)
        yield s
    finally:
        s.close()


def release_body(**extra) -> dict:
    return {
        "mode": "release",
        "build_name": "1.0.1",
        "platform": "ios",
        "confirm_build_number": "185",
        **extra,
    }


def code_of(resp) -> tuple[int, str | None]:
    status, body = resp
    return status, (body or {}).get("code")


def golive_jobs(srv: GoliveServer) -> list:
    return srv.store.list_jobs_by_preset([GOLIVE], 50)


# ── plan 모드 ─────────────────────────────────────────────────────────────────


def test_plan_takes_a_client_token_and_sends_the_role_inputs(gsrv):
    status, body = gsrv.golive({"build_name": "1.0.1", "platform": "both"}, token="alice")
    assert status == 202, body
    assert body["submission"]["cancel_token"]  # 취소 비밀이 응답에 실린다
    job = gsrv.store.get_job(body["job_id"])
    assert job.preset == GOLIVE
    assert job.inputs == {
        "build_name": "1.0.1",
        "build_name_android": "",
        "confirm_build_number": "",
        "mode": "plan",
        "platform": "both",
    }


def test_plan_with_a_time_is_400(gsrv):
    for when in ({"start_at": "15:00"}, {"start_in_seconds": 600}):
        status, body = gsrv.golive({"build_name": "1.0.1", **when}, token="alice")
        assert status == 400, body
        assert "plan" in body["error"]
    assert golive_jobs(gsrv) == []


def test_golive_needs_the_role_and_the_gate(tmp_path, remote, monkeypatch):
    s = GoliveServer(tmp_path, remote)
    try:
        assert code_of(s.golive({"build_name": "1.0.1"}, token="alice")) == (
            409,
            "setup_incomplete",
        )
        s.open_gate(monkeypatch)
        profile = {**PROFILE}  # golive 역할이 빈 프로파일
        s.cfg.repos = (
            RepoConfig(name="app", url=remote.url, release=parse_release_profile("app", profile)),
        )
        assert code_of(s.golive({"build_name": "1.0.1"}, token="alice")) == (
            409,
            "role_not_configured",
        )
    finally:
        s.close()


# ── release 모드의 문(S14) ────────────────────────────────────────────────────


def test_release_needs_an_admin_token(gsrv):
    gsrv.golive_plan_job()
    assert code_of(gsrv.golive(release_body(), token="alice")) == (403, "admin_required")
    assert len(golive_jobs(gsrv)) == 1  # plan 잡 하나뿐 — release 잡은 생기지 않았다


def test_release_without_a_plan_for_that_build_is_golive_plan_required(gsrv):
    assert code_of(gsrv.golive(release_body())) == (409, "golive_plan_required")
    gsrv.golive_plan_job({**GOLIVE_PLAN, "build_name": "1.0.0"})
    assert code_of(gsrv.golive(release_body())) == (409, "golive_plan_required")
    # 실패한 plan(스토어를 못 읽음)도 문을 열지 않는다
    gsrv.golive_plan_job(state="failed")
    assert code_of(gsrv.golive(release_body())) == (409, "golive_plan_required")


def test_a_stale_plan_is_golive_plan_stale(gsrv):
    gsrv.golive_plan_job({**GOLIVE_PLAN, "measured_at": "2026-09-17T11:00:00Z"})
    status, body = gsrv.golive(release_body())
    assert (status, body["code"]) == (409, "golive_plan_stale"), body
    assert body["age_seconds"] == 3600


def test_blocked_looks_only_at_the_chosen_platform(gsrv):
    """S21 — Android 가 심사 중이어도 iOS 만 고른 release 는 통과한다."""
    gsrv.golive_plan_job()
    for platform in ("android", "both"):
        status, body = gsrv.golive(release_body(platform=platform))
        assert (status, body["code"]) == (409, "golive_plan_blocked"), body
        assert body["platforms"] == ["android"]
    status, body = gsrv.golive(release_body(platform="ios"))
    assert status == 202, body
    job = gsrv.store.get_job(body["job_id"])
    assert job.inputs["mode"] == "release" and job.inputs["platform"] == "ios"
    assert job.inputs["confirm_build_number"] == "185"


def test_the_typed_number_must_equal_the_approved_build_and_auto_is_refused(gsrv):
    gsrv.golive_plan_job()
    for typed in ("184", "auto", "", " ", "18x"):
        status, body = gsrv.golive(release_body(confirm_build_number=typed))
        assert (status, body["code"]) == (409, "build_number_mismatch"), (typed, body)
    assert len(golive_jobs(gsrv)) == 1  # plan 잡 하나뿐


def test_both_needs_the_number_to_match_both_approved_builds(gsrv):
    split = {
        **BOTH_APPROVED,
        "platforms": {"ios": IOS_APPROVED, "android": {**ANDROID_APPROVED, "approved_build": 186}},
    }
    gsrv.golive_plan_job(split)
    for typed in ("185", "186"):
        status, body = gsrv.golive(release_body(platform="both", confirm_build_number=typed))
        assert (status, body["code"]) == (409, "build_number_mismatch"), body
        assert "separately" in body["error"]  # 플랫폼별로 따로 걸라고 말한다
        assert body["approved_build"] == {"ios": 185, "android": 186}
    gsrv.golive_plan_job(BOTH_APPROVED)
    status, body = gsrv.golive(release_body(platform="both"))
    assert status == 202, body


def test_a_plan_that_did_not_read_the_chosen_platform_does_not_open_it(gsrv):
    gsrv.golive_plan_job({**GOLIVE_PLAN, "platforms": {"ios": IOS_APPROVED}})
    assert code_of(gsrv.golive(release_body(platform="android"))) == (
        409,
        "golive_plan_required",
    )


# ── 예약 · 중복(S14 · S24) ────────────────────────────────────────────────────


def test_a_scheduled_release_is_201_with_the_cancel_secret_and_its_time(gsrv):
    gsrv.golive_plan_job()
    status, body = gsrv.golive(release_body(start_in_seconds=3 * 3600))
    assert status == 201, body
    assert body["state"] == SCHEDULED
    assert body["not_before"] == "2026-09-17T15:00:00Z"
    assert body["submission"]["cancel_token"] and body["submission"]["id"]
    job = gsrv.store.get_job(body["job_id"])
    assert job.state == SCHEDULED and job.inputs["mode"] == "release"


def test_a_morning_booking_for_the_afternoon_is_not_stopped_by_the_plan_age(gsrv):
    """S14 — 낡음 검사는 거는 순간에만 한다. 오전 10시에 건 오후 3시 예약은 실행 때 막히지
    않는다."""
    gsrv.golive_plan_job()
    status, body = gsrv.golive(release_body(start_in_seconds=5 * 3600))
    assert status == 201, body
    later = NOW + timedelta(hours=5, seconds=1)
    gsrv.app.now_fn = lambda: later
    expired, promoted = gsrv.app.tick_schedule(later)
    assert promoted == [body["job_id"]] and expired == []
    assert gsrv.store.get_job(body["job_id"]).state == QUEUED


def test_a_live_golive_chain_refuses_a_second_release_now_or_later(gsrv):
    """S24 — 같은 build_name 의 출시가 예약이거나 살아 있으면 409 `golive_running`."""
    gsrv.golive_plan_job(BOTH_APPROVED)
    status, first = gsrv.golive(release_body(start_in_seconds=3600))
    assert status == 201, first
    for extra in ({}, {"start_in_seconds": 7200}, {"platform": "android"}):
        status, body = gsrv.golive(release_body(**extra))
        assert (status, body["code"]) == (409, "golive_running"), body
        assert body["job_id"] == first["job_id"]
    # 시각이 되어 큐에 올라가도(활성) 똑같이 막는다
    gsrv.app.tick_schedule(NOW + timedelta(hours=1, seconds=1))
    assert gsrv.store.get_job(first["job_id"]).state == QUEUED
    assert code_of(gsrv.golive(release_body())) == (409, "golive_running")
    # plan 은 막지 않는다 — 읽기만 한다
    assert gsrv.golive({"build_name": "1.0.1"}, token="alice")[0] == 202
    # 다른 build_name 은 상관없다
    gsrv.golive_plan_job({**BOTH_APPROVED, "build_name": "1.0.2"})
    assert gsrv.golive(release_body(build_name="1.0.2"))[0] == 202


def test_a_cancelled_booking_frees_the_build(gsrv):
    gsrv.golive_plan_job()
    status, first = gsrv.golive(release_body(start_in_seconds=3600))
    assert status == 201, first
    cancel = gsrv.req("POST", f"/jobs/{first['job_id']}/cancel", token="admin", json_body={})
    assert cancel[0] == 200, cancel
    assert gsrv.golive(release_body())[0] == 202


# ── 제출된 초안(결정 216) ────────────────────────────────────────────────────


def _draft(srv: GoliveServer, state: str) -> int:
    return srv.store.create_version(
        repo="app",
        ios_version="1.0.1",
        android_version="1.0.1",
        state=state,
        created_by="macmini-admin",
        now=NOW,
        expires_at=NOW + timedelta(days=1),
    )


def test_a_submitted_draft_takes_golive_and_a_discarded_one_stays_closed(gsrv):
    gsrv.golive_plan_job()
    submitted = _draft(gsrv, "submitted")
    status, body = gsrv.golive({"version_id": submitted, "platform": "ios"}, token="alice")
    assert status == 202, body
    status, body = gsrv.golive(
        {"version_id": submitted, "mode": "release", "platform": "ios", "confirm_build_number": 185}
    )
    assert status == 202, body
    discarded = _draft(gsrv, "discarded")
    assert code_of(gsrv.golive({"version_id": discarded}, token="alice")) == (
        409,
        "version_closed",
    )
    # 다른 역할은 지금처럼 제출된 초안을 닫는다
    assert code_of(gsrv.post("review", {"version_id": submitted})) == (409, "version_closed")


# ── 일반 경로 막기(S23 · 결정 235) ────────────────────────────────────────────


def test_post_jobs_refuses_golive_release_for_everyone(gsrv):
    for token in ("alice", "admin"):
        status, body = gsrv.post_jobs(
            GOLIVE,
            {"build_name": "1.0.1", "mode": "release", "confirm_build_number": "185"},
            token=token,
        )
        assert (status, body["code"]) == (409, "release_route_required"), body
        assert "release/golive" in body["error"]
    assert golive_jobs(gsrv) == []
    # plan 은 일반 경로로도 돈다
    assert gsrv.post_jobs(GOLIVE, {"build_name": "1.0.1"})[0] == 201


def test_post_jobs_still_takes_upload_and_review_the_driver_relies_on_it(gsrv):
    status, body = gsrv.post_jobs(
        "release-upload",
        {"build_name": "1.0.1", "mode": "upload", "confirm_build_number": "181"},
    )
    assert status == 201, body
    status, body = gsrv.post_jobs(
        "release-review",
        {"build_name": "1.0.1", "mode": "submit", "confirm_build_number": "181"},
    )
    assert status == 201, body


def test_a_preset_that_is_not_a_golive_role_is_not_refused(gsrv):
    """막는 것은 프로파일이 golive 역할로 부르는 프리셋뿐이다 — 이름이 아니라 역할로 찾는다."""
    gsrv.cfg.presets = (
        parse_preset(preset("other-golive", GOLIVE_INPUTS)),
        *gsrv.cfg.presets,
    )
    status, body = gsrv.post_jobs(
        "other-golive", {"build_name": "1.0.1", "mode": "release", "confirm_build_number": "1"}
    )
    assert status == 201, body


# ── 격리 검증 P2 (#255) ───────────────────────────────────────────────────────


def test_six_simultaneous_releases_of_one_build_make_exactly_one_job(gsrv):
    """결정 232 — 확인과 제출이 한 락 안에 있다. 같은 release 를 여섯이 동시에 보내도 잡은
    하나다."""

    gsrv.golive_plan_job()
    barrier = threading.Barrier(6)
    results: list[tuple[int, str | None]] = []
    lock = threading.Lock()

    def send() -> None:
        barrier.wait()
        resp = gsrv.golive(release_body())
        with lock:
            results.append(code_of(resp))

    threads = [threading.Thread(target=send) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert sorted(results) == [(202, None)] + [(409, "golive_running")] * 5, results
    releases = [j for j in golive_jobs(gsrv) if j.inputs.get("mode") == "release"]
    assert len(releases) == 1, releases


def test_post_jobs_refuses_a_golive_preset_whose_mode_defaults_to_release(gsrv):
    """결정 235 — 막는 것은 검증을 지난 입력이다. 본문에 mode 가 없어도 기본값이 release 면
    일반 경로로는 출시가 되지 않는다(`rcm check` 가 FAIL 로 그리는 프리셋이어도)."""
    loose = [*GOLIVE_INPUTS[:3], {**GOLIVE_INPUTS[3], "default": "release"}, GOLIVE_INPUTS[4]]
    gsrv.cfg.presets = tuple(
        parse_preset(preset(GOLIVE, loose)) if p.name == GOLIVE else p for p in gsrv.cfg.presets
    )
    status, body = gsrv.post_jobs(GOLIVE, {"build_name": "1.0.1", "confirm_build_number": "185"})
    assert (status, body.get("code")) == (409, "release_route_required"), body
    assert golive_jobs(gsrv) == []


def test_a_plan_measured_in_the_future_is_stale_not_fresh_for_ever(gsrv):
    """plan 의 `measured_at` 이 지금보다 뒤면(시계가 틀린 스크립트) 신선하다고 믿지 않는다."""
    gsrv.golive_plan_job({**GOLIVE_PLAN, "measured_at": "2026-09-18T12:00:00Z"})
    status, body = gsrv.golive(release_body())
    assert (status, body["code"]) == (409, "golive_plan_stale"), body


# ── D(#256)의 반복과 함께 (결정 210 · 232 · 236) ───────────────────────────────


def test_a_golive_release_that_exits_75_is_retried_and_the_chain_keeps_the_build_busy(
    tmp_path, remote, monkeypatch
):
    """release 가 75(승인 뒤 남은 일)로 끝나면 `retried` 로 닫히고 같은 입력의 다음 시도가 예약된다.
    `golive_running` 은 그 사슬의 `scheduled` 시도를 살아 있는 것으로 보고 새 release 를 막는다.
    「출시 예약」 목록에도 그 다음 시도가 나온다."""
    from remote_ci_monitor.core.model import RETRIED
    from remote_ci_monitor.worker import outcome_for

    s = GoliveServer(tmp_path, remote)
    try:
        s.open_gate(monkeypatch)
        retrying = {
            **preset(GOLIVE, GOLIVE_INPUTS),
            "retry": {"on_exit": 75, "interval_seconds": 600, "max_attempts": 12},
        }
        s.cfg.presets = tuple(
            parse_preset(retrying) if p.name == GOLIVE else p for p in s.cfg.presets
        )
        s.golive_plan_job(BOTH_APPROVED)
        status, first = s.golive(release_body())
        assert status == 202, first
        root = first["job_id"]
        job = s.store.get_job(root)
        assert (job.retry_on_exit, job.retry_interval_seconds, job.retry_max_attempts) == (
            75,
            600,
            12,
        )
        # 워커가 집어 돌렸고 스크립트가 75 로 끝났다(iOS 출시 처리 중)
        started = NOW + timedelta(minutes=1)
        s.store._conn().execute(
            "UPDATE jobs SET state='running', started_at=?, lane=1 WHERE id=?",
            (started.timestamp(), root),
        )
        ended = started + timedelta(minutes=1)
        oc = outcome_for(s.store.get_job(root), [], started=started, finished=ended, rc=75)
        assert oc.state == RETRIED
        assert s.store.finish(root, RETRIED, now=ended, exit_code=75)
        nxt_id = s.store.get_job(root).retried_as
        nxt = s.store.get_job(nxt_id)
        assert nxt.state == SCHEDULED and nxt.chain_id == root and nxt.attempt == 2
        assert nxt.not_before == ended + timedelta(seconds=600)
        assert nxt.inputs == job.inputs  # 같은 build · 같은 플랫폼 · 같은 친 번호
        # 사슬이 살아 있다 — 지금도, 나중에도, 다른 플랫폼으로도 새 release 는 409
        for extra in ({}, {"start_in_seconds": 7200}, {"platform": "android"}):
            status, body = s.golive(release_body(**extra))
            assert (status, body["code"]) == (409, "golive_running"), body
            assert body["job_id"] == nxt_id and body["state"] == SCHEDULED
        live = s.req("GET", "/api/repos/app/release")[1]["golive"]["live"]
        assert [(r["job_id"], r["state"]) for r in live] == [(nxt_id, SCHEDULED)]
    finally:
        s.close()


@pytest.mark.parametrize("rc", [1, 2, 3])
def test_a_golive_release_that_ends_1_2_or_3_is_never_retried(tmp_path, remote, monkeypatch, rc):
    """`ae3ce59` 의 짝(결정 201 · 236). 같은 반복 정책이어도 2(미승인)는 다시 돌지 않는다.
    75 로 승인을 기다리면 승인을 감시하는 자동 출시가 되기 때문이다. 1(실패) · 3(번호 불일치)도
    마찬가지다 — `failed` 로 끝나고 다음 시도가 없으며 build 는 다시 열린다."""
    from remote_ci_monitor.core.model import FAILED
    from remote_ci_monitor.worker import outcome_for

    s = GoliveServer(tmp_path, remote)
    try:
        s.open_gate(monkeypatch)
        retrying = {
            **preset(GOLIVE, GOLIVE_INPUTS),
            "retry": {"on_exit": 75, "interval_seconds": 600, "max_attempts": 12},
        }
        s.cfg.presets = tuple(
            parse_preset(retrying) if p.name == GOLIVE else p for p in s.cfg.presets
        )
        s.golive_plan_job(BOTH_APPROVED)
        status, first = s.golive(release_body())
        assert status == 202, first
        root = first["job_id"]
        started = NOW + timedelta(minutes=1)
        s.store._conn().execute(
            "UPDATE jobs SET state='running', started_at=?, lane=1 WHERE id=?",
            (started.timestamp(), root),
        )
        ended = started + timedelta(minutes=1)
        oc = outcome_for(s.store.get_job(root), [], started=started, finished=ended, rc=rc)
        assert oc.state == FAILED, oc
        assert s.store.finish(root, oc.state, now=ended, exit_code=rc)
        assert s.store.get_job(root).retried_as is None
        assert [j.id for j in golive_jobs(s) if j.state == SCHEDULED] == []
        # 사슬이 끝났다 — 같은 build 의 새 release 를 다시 받는다
        assert s.golive(release_body())[0] == 202
    finally:
        s.close()
