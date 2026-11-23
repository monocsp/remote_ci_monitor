"""M5s PR E — `status` 역할의 라우트 · `GET …/release` 의 `status` · 역할별 창(§3 #11) · 검토 패널.

명세: docs/m5s-workplan.md §4.4 · §7 S15.

여기서 지키는 것:
- `POST …/release/status` 는 클라이언트 토큰으로 받고 정규 `POST /jobs` 경로로 status 프리셋의
  잡을 낸다. `build_name` 은 선택이다(비우면 스크립트가 최근 버전들을 읽는다). 워커 토큰은 403,
  토큰이 없으면 401, 역할이 비면 409 `role_not_configured`, 게이트가 닫혀 있으면 409
  `setup_incomplete`.
- 서버는 묶음의 `status.json` 을 `GET …/release` 의 `status` 로 싣는다(`checked_at` 으로 잰다).
- 창: 자주 도는 status 잡이 500개를 넘어도 plan · review 의 최신 문서가 사라지지 않는다. 역할
  프리셋마다 따로 창을 연다. `jobs[]` 에서도 status 행이 다른 역할의 행을 밀어내지 않는다.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from jobfactory import NOW as PURE_NOW
from remote_ci_monitor.config import RepoConfig, parse_preset, parse_release_profile
from remote_ci_monitor.core.model import FAILED, QUEUED, RUNNING, SUCCEEDED, Requester, Source
from remote_ci_monitor.release_state import (
    JOBS_LIMIT,
    ROLE_WINDOW,
    STATUS_ROWS_LIMIT,
    release_view,
    role_windows,
)
from test_release_state import PRESETS as PURE_PRESETS
from test_release_state import STATUS, files, git_job, reader
from test_server_release_routes import NOW, PROFILE, ReleaseServer, preset
from test_server_release_routes import remote as _remote

remote = _remote  # 픽스처를 이 모듈의 이름공간에 — pytest 는 이름으로 찾는다

pytestmark = pytest.mark.skipif(__import__("shutil").which("git") is None, reason="no git")

STATUS_PRESET = preset("release-status", [{"name": "build_name", "type": "string", "default": ""}])
STATUS_DOC = {
    **STATUS,
    "checked_at": "2026-09-17T11:58:00Z",
    "ios": {**STATUS["ios"], "state": "waiting_for_review"},
}


class StatusServer(ReleaseServer):
    """`profile.presets.status` 가 있는 `app` — 나머지는 `ReleaseServer` 그대로."""

    def __init__(self, tmp_path, remote, *, with_status: bool = True, **overrides):
        super().__init__(tmp_path, remote, **overrides)
        raw = json.loads(json.dumps(PROFILE))
        if with_status:
            raw["presets"] = {**raw["presets"], "status": "release-status"}
        self.cfg.repos = (
            RepoConfig(name="app", url=remote.url, release=parse_release_profile("app", raw)),
        )
        self.cfg.presets = (parse_preset(STATUS_PRESET), *self.cfg.presets)

    def bare_jobs(self, preset_name: str, count: int, *, state: str = SUCCEEDED) -> list[int]:
        """묶음 없는 끝난 잡 `count` 개를 빠르게 — 창을 채우는 데만 쓴다."""
        sha = self.remote.main
        src = Source(mode="git_ref", repo="app", ref="main", sha=sha, base_sha=sha, dirty=False)
        ids = []
        for i in range(count):
            job = self.store.create_job(
                preset=preset_name,
                inputs={"build_name": ""},
                key=f"{preset_name}#{i}",
                concurrency_group=None,
                source=src,
                requester=Requester(name="alice-laptop", label="store:alice-laptop"),
                timeout_seconds=600,
                join_key=None,
                now=NOW - timedelta(minutes=2),
                state=QUEUED,
            )
            self.store._conn().execute(
                "UPDATE jobs SET state=?, started_at=?, finished_at=? WHERE id=?",
                (
                    state,
                    (NOW - timedelta(minutes=2)).timestamp(),
                    (NOW - timedelta(minutes=1)).timestamp(),
                    job.id,
                ),
            )
            ids.append(job.id)
        return ids


@pytest.fixture
def ssrv(tmp_path, remote):
    s = StatusServer(tmp_path, remote)
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def open_ssrv(ssrv, monkeypatch):
    ssrv.open_gate(monkeypatch)
    return ssrv


# ── POST …/release/status ─────────────────────────────────────────────────────


def test_release_status_panel_route_submits_the_status_preset_through_the_job_path(
    open_ssrv, remote
):
    srv = open_ssrv
    status, body = srv.post("status", {"build_name": "1.2.0"})
    assert status == 202, body
    assert body == {"job_id": 1, "joined": False, "state": "queued", "sha": remote.main}
    job = srv.store.get_job(1)
    assert job.preset == "release-status" and job.inputs.get("build_name") == "1.2.0"
    assert job.source.mode == "git_ref" and job.source.ref == "main"
    assert job.requester.label == "store:alice-laptop"
    # build_name 은 선택이다 — 비우면 스크립트가 최근 버전들을 읽는다(§4.4)
    status, body = srv.post("status", {})
    assert status == 202, body
    assert srv.store.get_job(body["job_id"]).inputs.get("build_name", "") == ""
    # 이상한 이름은 400 — 셸에 닿기 전에
    assert srv.post("status", {"build_name": "../x"})[0] == 400


def test_release_status_panel_route_is_a_client_token_write(open_ssrv):
    srv = open_ssrv
    path = "/api/repos/app/release/status"
    assert srv.req("POST", path, json_body={})[0] == 401
    secret = srv.store.add_token("build-02", admin=False, now=NOW, kind="worker")
    assert srv.req("POST", path, token=secret, json_body={})[0] == 403
    assert srv.req("GET", path, token="alice")[0] == 405
    assert srv.req("POST", path + "/validate", token="alice", json_body={})[0] == 404
    # admin 이 아니어도 된다 — 읽기만 하는 역할이다
    assert srv.post("status", {}, token="bob")[0] == 202


def test_release_status_panel_route_needs_the_role_and_an_open_gate(tmp_path, remote, monkeypatch):
    closed = StatusServer(tmp_path / "a", remote)
    try:
        status, body = closed.post("status", {})
        assert (status, body.get("code")) == (409, "setup_incomplete"), body
    finally:
        closed.close()
    unset = StatusServer(tmp_path / "b", remote, with_status=False)
    try:
        unset.open_gate(monkeypatch)
        status, body = unset.post("status", {})
        assert (status, body.get("code")) == (409, "role_not_configured"), body
        assert body.get("role") == "status"
    finally:
        unset.close()


# ── GET …/release 의 status (S15) ─────────────────────────────────────────────


def test_release_status_panel_view_carries_the_status_document(open_ssrv):
    srv = open_ssrv
    jid = srv.role_job("release-status", {"out/status.json": json.dumps(STATUS_DOC).encode()})
    status, body = srv.req("GET", "/api/repos/app/release")
    assert status == 200, body
    assert body["status"] == {
        "job_id": jid,
        "state": "succeeded",
        "measured_at": "2026-09-17T11:58:00Z",
        "age_seconds": 120,
        "stale": False,
        "doc": STATUS_DOC,
    }
    assert [(r["id"], r["role"]) for r in body["jobs"]] == [(jid, "status")]
    # 버전 페이지의 `release` 도 같은 보기다
    assert body["plan"] is None


def test_release_status_panel_frequent_status_jobs_do_not_push_the_plan_out(open_ssrv):
    """§3 #11 — 예전에는 역할 프리셋 잡 500개를 **한 창**으로 읽었다. 5분마다 도는 status 잡이
    500개를 채우면 plan · review 의 최신 문서가 `null` 이 되고 되돌릴 수 없는 문이 닫힌다."""
    srv = open_ssrv
    plan_id = srv.plan_job()
    review_id = srv.review_plan_job()
    status_id = srv.role_job("release-status", {"status.json": json.dumps(STATUS_DOC).encode()})
    srv.bare_jobs("release-status", ROLE_WINDOW + 5, state=FAILED)
    body = srv.req("GET", "/api/repos/app/release")[1]
    assert body["plan"] is not None and body["plan"]["job_id"] == plan_id
    assert body["review"]["plan"] is not None and body["review"]["plan"]["job_id"] == review_id
    # status 자기 창은 지금처럼 500 — 그 밖의 옛 문서는 지어내지 않는다
    assert body["status"] is None, "status.json older than its own window"
    roles = [r["role"] for r in body["jobs"]]
    assert roles.count("status") <= STATUS_ROWS_LIMIT
    assert "plan" in roles and "review" in roles
    del status_id


# ── 순수 규칙(release_state) ──────────────────────────────────────────────────


def test_release_status_panel_role_windows_open_one_window_per_preset():
    windows = role_windows(PURE_PRESETS)
    # 산출물 파일이 있는 역할은 자기 창 500, 행만 있는 역할(gate · qa · dev)은 행 수만큼
    assert windows["release-plan"] == ROLE_WINDOW == 500
    assert windows["release-status"] == ROLE_WINDOW
    assert windows["release-golive"] == ROLE_WINDOW
    assert set(windows) == {n for n in PURE_PRESETS.values() if n}
    rows_only = role_windows({"gate": "gate-smoke", "plan": "release-plan"})
    assert rows_only == {"gate-smoke": JOBS_LIMIT, "release-plan": ROLE_WINDOW}
    # 한 프리셋이 두 역할을 맡으면 큰 창
    assert role_windows({"gate": "p", "plan": "p"}) == {"p": ROLE_WINDOW}


def _pure_view(jobs, bundles, docs, *, names_seen=None):
    def names_of(jid):
        if names_seen is not None:
            names_seen.append(jid)
        return bundles.get(jid, [])

    return release_view(
        jobs, PURE_PRESETS, names_of, reader(docs), now=PURE_NOW, max_age_minutes=30
    )


def test_release_status_panel_status_rows_do_not_crowd_jobs():
    """`jobs[]` 는 새것부터 50 — status 행은 그중 최근 몇 개만 차지한다(Store 탭의 작업 목록과
    `roleFinishedAt` 이 그 행에서 시각을 읽는다)."""
    status_jobs = [git_job(i, "release-status") for i in range(200, 100, -1)]
    plan_jobs = [git_job(i, "release-plan", finished_min=60) for i in range(60, 0, -1)]
    v = _pure_view([git_job(201, "release-status", RUNNING), *status_jobs, *plan_jobs], {}, {})
    ids = [r["id"] for r in v["jobs"]]
    roles = [r["role"] for r in v["jobs"]]
    assert ids == sorted(ids, reverse=True), "still newest first"
    assert roles.count("status") == STATUS_ROWS_LIMIT
    assert ids[:STATUS_ROWS_LIMIT] == [201, 200, 199], "the newest status rows stay"
    assert roles.count("plan") == JOBS_LIMIT, "plan rows keep their 50"


def test_release_status_panel_unneeded_status_bundles_are_not_read():
    """5분마다 도는 status 잡 500개의 묶음 목록을 매번 다 읽지 않는다 — 행이 차고 문서를 찾았으면
    그 잡은 건너뛴다."""
    jobs = [git_job(i, "release-status") for i in range(500, 0, -1)]
    seen: list[int] = []
    v = _pure_view(
        jobs,
        {500: files("status.json")},
        {(500, "status.json"): json.dumps(STATUS).encode()},
        names_seen=seen,
    )
    assert v["status"]["job_id"] == 500
    assert len(seen) <= STATUS_ROWS_LIMIT, seen


def test_release_status_panel_full_status_rows_still_find_an_older_status_document():
    """status 행 3개가 묶음 없는 잡으로 찼어도 더 오래된 잡의 status.json 을 찾는다 — 행이
    찼다고 문서 찾기까지 건너뛰면 status 가 null 이 된다(#254 격리 검증 P1)."""
    jobs = [git_job(i, "release-status", FAILED) for i in range(205, 200, -1)]
    jobs.append(git_job(200, "release-status"))
    v = _pure_view(
        jobs, {200: files("status.json")}, {(200, "status.json"): json.dumps(STATUS).encode()}
    )
    assert [r["id"] for r in v["jobs"]] == [205, 204, 203]
    assert v["status"] is not None and v["status"]["job_id"] == 200


def test_release_status_panel_full_rows_still_find_an_older_plan_document():
    """plan 잡보다 새 다른 역할 잡이 50개를 넘어도 plan.json 을 찾는다."""
    uploads = [git_job(i, "release-upload") for i in range(170, 100, -1)]
    plan = {"schema": 1, "build_name": "1.0.1", "n": 181, "measured_at": "2026-09-04T00:48:12Z"}
    v = _pure_view(
        [*uploads, git_job(100, "release-plan")],
        {100: files("plan.json")},
        {(100, "plan.json"): json.dumps(plan).encode()},
    )
    assert len(v["jobs"]) == JOBS_LIMIT and 100 not in {r["id"] for r in v["jobs"]}
    assert v["plan"] is not None and v["plan"]["job_id"] == 100


def test_release_status_panel_jobs_from_several_presets_are_newest_first(open_ssrv):
    """서버는 프리셋마다 따로 읽어 합친다 — 합친 목록이 새것부터여야 `jobs[]` 도, 「역할의
    최신」도 맞다."""
    srv = open_ssrv
    ids = [
        srv.plan_job(),
        srv.role_job("release-status", {"status.json": json.dumps(STATUS_DOC).encode()}),
        srv.role_job("release-upload", None),
        srv.plan_job(),
        srv.role_job("release-status", None, state=FAILED),
        srv.role_job("release-upload", None),
    ]
    body = srv.req("GET", "/api/repos/app/release")[1]
    got = [r["id"] for r in body["jobs"]]
    assert got == sorted(ids, reverse=True), got
    assert body["plan"]["job_id"] == ids[3]
    assert body["status"]["job_id"] == ids[1]
