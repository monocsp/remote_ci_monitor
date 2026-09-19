"""버전 페이지 서버 라우트 — `/api/repos/<name>/release/versions[/<id>[/listing|/diff]]` 와 plan ·
review · upload · start 의 `version_id`.

명세: docs/version-page-workplan.md §2.2 · §11 · §7 AC-B2~B9 · AC-B11 · §8 E1 · E3~E6 · E9~E11 ·
E14 · E15 · E25.

여기서 지키는 것:
- GET 은 읽기 규칙(워커 토큰 거부) · POST · PUT · DELETE 는 admin(403 `admin_required`).
- 409 표: `version_exists` · `version_closed` · `version_running` · `setup_incomplete` ·
  `listing_json_unsupported` · `split_version_unsupported`; 400 은 `empty` · `pattern` ·
  `not_greater` · `listing_key` · `listing_too_large` · `build_name_mismatch`.
- 두 스토어 버전 이름(§11): 다를 때만 `build_name_android` 를 함께 보내고, 프리셋이 그 입력을
  모르면 제출을 거절한다.
- 잡 완료 훅: create 잡의 `version.json` + `prefill.json` 이 행을 `editing` 으로, 실패는 `failed`
  + `error`; delete 잡 0 → `discarded`; 심사 잡 → `editing`(submit 성공은 `submitted`).
- 서버 재시작 복구 · 드라이버에 `--version-id`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from datetime import timedelta
from pathlib import Path

import pytest

from gitrepo import RemoteRepo
from remote_ci_monitor.collect import CollectResult
from remote_ci_monitor.config import parse_preset, parse_release_profile
from remote_ci_monitor.core import artifacts as art
from remote_ci_monitor.core.model import FAILED, LOST, SUCCEEDED
from test_server_release_routes import (
    NOW,
    PLAN_DOC,
    PROFILE,
    REVIEW_INPUTS,
    REVIEW_PLAN_DOC,
    ReleaseServer,
    make_tar,
    preset,
    wait_run,
)
from test_server_release_routes import remote as _remote

remote = _remote  # 픽스처를 이 모듈의 이름공간에 — pytest 는 이름으로 찾는다

CHOICE = "choice"
VERSION_PRESET = preset(
    "release-version",
    [
        {
            "name": "mode",
            "type": CHOICE,
            "choices": ["prefill", "create", "delete"],
            "default": "prefill",
        },
        {"name": "ios_version", "type": "string", "default": ""},
        {"name": "android_version", "type": "string", "default": ""},
        {"name": "asc_version_id", "type": "string", "default": ""},
    ],
)
LISTING_JSON_INPUT = {"name": "listing_json", "type": "string", "default": ""}
#: 워크플랜 §11 — 두 스토어 이름이 다를 때만 오는 입력. 기본값은 빈 문자열이다.
BUILD_NAME_ANDROID_INPUT = {"name": "build_name_android", "type": "string", "default": ""}
REVIEW_LISTING_PRESET = preset(
    "release-review-listing",
    [*REVIEW_INPUTS, LISTING_JSON_INPUT, BUILD_NAME_ANDROID_INPUT],
)


def upload_preset(*extra: dict) -> dict:
    """`release-upload-listing` — 기본 입력 셋에 시험이 고른 것만 더한 upload 프리셋."""
    return preset(
        "release-upload-listing",
        [
            {"name": "build_name", "type": "string"},
            {"name": "confirm_build_number", "type": "string", "default": ""},
            {
                "name": "mode",
                "type": CHOICE,
                "choices": ["rehearsal", "upload"],
                "default": "rehearsal",
            },
            *extra,
        ],
    )


#: 버전 프리셋 + listing_json 을 받는 review 프리셋
PROFILE_V = {
    **PROFILE,
    "presets": {
        **PROFILE["presets"],
        "version": "release-version",
        "review": "release-review-listing",
    },
    "version_ttl_hours": 24,
}
#: 버전 프리셋 없음(E25) — review 는 listing_json 을 모르는 기존 프리셋
PROFILE_NO_VERSION = {**PROFILE}
LIVE_PLAN = {
    **PLAN_DOC,
    "build_name": "1.1.0",
    "store": {"asc_live": "1.1.0", "play": {"production": 180, "production_name": "1.0.0"}},
}
VERSION_DOC = {
    "schema": 1,
    "mode": "create",
    "ios": {"version": "1.1.1", "asc_version_id": "abc123", "state": "PREPARE_FOR_SUBMISSION"},
    "android": {"version": "1.0.1"},
    "measured_at": "2026-09-18T02:00:00Z",
}
PREFILL_DOC = {
    "schema": 1,
    "source": "asc_live:1.1.0 · play_listing",
    "locale": "ko",
    "ios": {
        "subtitle": "Sleep better",
        "promotional_text": "",
        "description": "A long description.\r\n",
        "keywords": "sleep,mood",
        "support_url": "https://example.com/support",
        "marketing_url": "",
        "whats_new": "Bug fixes",
        "screenshots": [{"path": "store/screenshots/ios/ko/0.png"}],
    },
    "android": {
        "title": "Dolomood",
        "short_description": "Track your mood",
        "full_description": "Full text",
        "whats_new": "Bug fixes",
        "graphics": [],
    },
}
VPATH = "/api/repos/app/release/versions"


class VersionServer(ReleaseServer):
    """버전 프리셋과 listing_json 을 받는 review 프리셋이 있는 `app`."""

    def __init__(self, tmp_path: Path, remote: RemoteRepo, profile: dict = PROFILE_V):
        super().__init__(tmp_path, remote)
        self.cfg.presets = (
            tuple(parse_preset(p) for p in (VERSION_PRESET, REVIEW_LISTING_PRESET))
            + self.cfg.presets
        )
        self.set_profile(profile)

    def set_profile(self, profile: dict) -> None:
        self.cfg.repos[0].release = parse_release_profile("app", profile)

    def create(self, body: dict, token: str = "admin"):
        return self.req("POST", VPATH, token=token, json_body=body)

    def version(self, vid: int):
        return self.req("GET", f"{VPATH}/{vid}")[1]

    def put_listing(self, vid: int, body: dict, token: str = "admin"):
        return self.req("PUT", f"{VPATH}/{vid}/listing", token=token, json_body=body)

    def discard(self, vid: int, token: str = "admin"):
        return self.req("DELETE", f"{VPATH}/{vid}", token=token)

    def finish_job(
        self,
        job_id: int,
        files: dict[str, bytes] | None,
        *,
        state: str = SUCCEEDED,
        exit_code: int | None = 0,
    ) -> None:
        """POST 가 낸(큐에 든) 잡을 워커처럼 끝낸다 — 묶음을 발행하고 상태 변화를 알린다(훅)."""
        self.store._conn().execute(
            "UPDATE jobs SET state='running', started_at=?, lane=1 WHERE id=?",
            ((NOW - timedelta(minutes=2)).timestamp(), job_id),
        )
        bundle = None
        if files is not None:
            payload = make_tar(files)
            d = self.cfg.data_dir / "artifacts" / str(job_id)
            d.mkdir(parents=True, exist_ok=True)
            (d / "bundle.tar").write_bytes(payload)
            entries = tuple(
                art.BundleFile(
                    path=p, size=len(b), sha256=hashlib.sha256(b).hexdigest(), mode=0o644
                )
                for p, b in files.items()
            )
            bundle = CollectResult(
                state=art.READY,
                files=entries,
                total_bytes=sum(len(b) for b in files.values()),
                bundle_bytes=len(payload),
                skipped_count=0,
                bundle_sha256=hashlib.sha256(payload).hexdigest(),
                bundle_path=d / "bundle.tar",
            )
        assert self.store.finish(job_id, state, now=NOW, exit_code=exit_code, bundle=bundle)
        self.app._on_job_change(job_id)

    def finish_quietly(self, job_id: int, files: dict[str, bytes] | None, **kw) -> None:
        """서버가 꺼진 동안 끝난 잡 — 훅이 돌지 않는다(재시작 복구 시험)."""
        real = self.app._version_job_finished
        self.app._version_job_finished = lambda _job: None
        try:
            self.finish_job(job_id, files, **kw)
        finally:
            self.app._version_job_finished = real


CREATE_FILES = {
    "out/version.json": json.dumps(VERSION_DOC).encode(),
    "out/prefill.json": json.dumps(PREFILL_DOC).encode(),
}


@pytest.fixture
def vsrv(tmp_path: Path, remote: RemoteRepo, monkeypatch):
    s = VersionServer(tmp_path, remote)
    try:
        s.open_gate(monkeypatch)
        yield s
    finally:
        s.close()


def code_of(resp) -> tuple[int, str | None]:
    status, body = resp
    return status, (body or {}).get("code")


def editing_draft(srv: VersionServer, ios="1.1.1", android="1.0.1") -> int:
    """create 잡까지 끝난 드래프트 하나 — `editing` · asc id · prefill 있음."""
    status, body = srv.create({"ios_version": ios, "android_version": android})
    assert status == 202, body
    doc = (
        VERSION_DOC
        if (ios, android) == ("1.1.1", "1.0.1")
        else {
            **VERSION_DOC,
            "ios": {**VERSION_DOC["ios"], "version": ios} if ios else None,
            "android": {"version": android} if android else None,
        }
    )
    files = {**CREATE_FILES, "out/version.json": json.dumps(doc).encode()}
    srv.finish_job(body["job_id"], files)
    row = srv.store.get_version(body["id"])
    assert row["state"] == "editing", row
    return body["id"]


# ── GET …/versions · POST …/versions (AC-B2 · B3 · B4 · E1 · E3 · E25) ──────────


def test_the_list_is_empty_and_answers_before_the_gate_opens(tmp_path, remote):
    s = VersionServer(tmp_path, remote)
    try:
        status, body = s.req("GET", VPATH)
        assert status == 200, body
        assert body == {
            "live": {"ios": None, "android": None, "from_plan_job": None},
            "hints": {"ios": None, "android": None},
            "ttl_hours": 24,
            "drafts": [],
            "history": [],
        }
        status, body = s.create({"ios_version": "1.1.1"})
        assert (status, body["code"]) == (409, "setup_incomplete")
        assert body["setup"]["complete"] is False
        assert s.req("GET", "/api/repos/nope/release/versions")[0] == 404
        assert s.req("GET", f"{VPATH}/1")[0] == 404
    finally:
        s.close()


def test_live_and_hints_come_from_the_latest_plan_and_creating_submits_the_version_preset(
    vsrv, remote
):
    """AC-B2 — 202 `{id, job_id}` · 행 `creating` · 잡 입력 `mode=create ios_version
    android_version`."""
    srv = vsrv
    plan_id = srv.plan_job(LIVE_PLAN)
    body = srv.req("GET", VPATH)[1]
    assert body["live"] == {"ios": "1.1.0", "android": "1.0.0", "from_plan_job": plan_id}
    assert body["hints"] == {"ios": "1.1.1", "android": "1.0.1"}
    status, body = srv.create({"ios_version": "1.1.1", "android_version": "1.0.1"})
    assert status == 202, body
    print("CREATE_JSON", json.dumps(body))
    assert body == {"id": 1, "job_id": body["job_id"], "state": "creating", "build_name": "1.1.1"}
    job = srv.store.get_job(body["job_id"])
    assert job.preset == "release-version" and job.state == "queued"
    assert job.inputs == {
        "mode": "create",
        "ios_version": "1.1.1",
        "android_version": "1.0.1",
        "asc_version_id": "",
    }
    assert job.source.mode == "git_ref" and job.source.sha == remote.main
    assert job.requester.name == "macmini-admin"
    row = srv.store.get_version(1)
    assert row["state"] == "creating" and row["create_job_id"] == job.id
    assert row["created_by"] == "macmini-admin" and row["created_at"] == NOW
    assert row["expires_at"] == NOW + timedelta(hours=24)
    listed = srv.req("GET", VPATH)[1]
    assert [d["id"] for d in listed["drafts"]] == [1]
    draft = listed["drafts"][0]
    print("DRAFT_JSON", json.dumps(draft))
    assert draft == {
        "id": 1,
        "repo": "app",
        "ios_version": "1.1.1",
        "android_version": "1.0.1",
        "build_name": "1.1.1",
        "state": "creating",
        "created_by": "macmini-admin",
        "created_at": "2026-09-17T12:00:00Z",
        "last_edit_at": None,
        "expires_at": "2026-09-18T12:00:00Z",
        "expired": False,
        "expiry_warned": False,
        "asc_version_id": None,
        "error": None,
        "create_job_id": job.id,
        "delete_job_id": None,
        "release_id": None,
        "review_job_id": None,
        "upload_job_id": None,
        "has_prefill": False,
        "has_edits": False,
        "changed": 0,
    }
    # 릴리스 보기의 jobs[] 에 역할 version 이 보인다
    view = srv.req("GET", "/api/repos/app/release")[1]
    assert [(j["id"], j["role"]) for j in view["jobs"]][0] == (job.id, "version")


def test_without_a_version_preset_the_draft_is_editing_at_once(vsrv):
    """AC-B3 · E25 — 프리셋 없음 + Android 만: 201 `job_id null` · 행 `editing` · prefill 없음."""
    srv = vsrv
    srv.set_profile(PROFILE_NO_VERSION)
    status, body = srv.create({"android_version": "1.0.1"})
    assert status == 201, body
    assert body == {"id": 1, "job_id": None, "state": "editing", "build_name": "1.0.1"}
    row = srv.store.get_version(1)
    assert row["state"] == "editing" and row["ios_version"] is None
    assert row["prefill_json"] is None and row["create_job_id"] is None
    assert srv.store.list_jobs_by_preset(["release-version"], 10) == []
    doc = srv.version(1)
    assert doc["prefill"] is None and doc["edited"] is None and doc["has_prefill"] is False
    # 파일 폴백은 `GET …/release/listing` 이 따로 답한다(§14-1) — 상세에는 없다
    assert "listing" not in doc
    assert srv.req("GET", "/api/repos/app/release/listing")[1]["configured"] is True


def test_name_checks_duplicates_and_permissions(vsrv):
    """AC-B4 — `1.0` 400 · 라이브 1.1.0 에 1.0.9 400 `not_greater` · 둘 다 빈 값 400 · 같은 이름의
    열린 드래프트 409 `version_exists` · 클라이언트 토큰 403 · E1: 플랜이 없으면 꼴만 본다."""
    srv = vsrv
    # E1 — 플랜 없음: 힌트 없음, 라이브 비교 없음
    assert srv.req("GET", VPATH)[1]["hints"] == {"ios": None, "android": None}
    assert code_of(srv.create({"ios_version": "1.0"})) == (400, "pattern")
    assert code_of(srv.create({"ios_version": "1.0.0-rc1"})) == (400, "pattern")
    assert code_of(srv.create({})) == (400, "empty")
    assert code_of(srv.create({"ios_version": "", "android_version": " "})) == (400, "empty")
    assert srv.create({"ios_version": 7})[0] == 400
    assert srv.req("POST", VPATH, token="admin", json_body=[])[0] == 400
    status, body = srv.create({"ios_version": "0.0.1"})  # 라이브를 모르면 작은 이름도 된다
    assert status == 202, body
    srv.plan_job(LIVE_PLAN)
    status, body = srv.create({"ios_version": "1.0.9"})
    assert (status, body["code"]) == (400, "not_greater") and body["live"] == "1.1.0"
    assert code_of(srv.create({"android_version": "1.0.0"})) == (400, "not_greater")
    assert code_of(srv.create({"ios_version": "1.1.1", "android_version": "0.9.0"})) == (
        400,
        "not_greater",
    )
    status, body = srv.create({"ios_version": "1.1.1", "android_version": "1.0.1"})
    assert status == 202, body
    # E3 — 같은 이름의 열린 드래프트
    status, dup = srv.create({"ios_version": "1.1.1"})
    assert (status, dup["code"]) == (409, "version_exists")
    assert dup["id"] == body["id"] and dup["state"] == "creating"
    assert code_of(srv.create({"ios_version": "1.1.2", "android_version": "1.0.1"})) == (
        409,
        "version_exists",
    )
    assert srv.create({"ios_version": "1.1.2", "android_version": "1.0.2"})[0] == 202
    # 버린 드래프트의 이름은 다시 쓸 수 있다
    srv.store.update_version(body["id"], state="discarded")
    assert srv.create({"ios_version": "1.1.1"})[0] == 202
    # 권한
    status, body = srv.create({"ios_version": "2.0.0"}, token="alice")
    assert (status, body["code"]) == (403, "admin_required")
    assert srv.req("POST", VPATH, json_body={"ios_version": "2.0.0"})[0] == 401
    worker = srv.store.add_token("build-02", admin=False, now=NOW, kind="worker")
    for method, path in [
        ("GET", VPATH),
        ("POST", VPATH),
        ("GET", f"{VPATH}/1"),
        ("DELETE", f"{VPATH}/1"),
        ("PUT", f"{VPATH}/1/listing"),
        ("GET", f"{VPATH}/1/diff"),
    ]:
        kw = {"json_body": {}} if method in ("POST", "PUT") else {}
        assert srv.req(method, path, token=worker, **kw)[0] == 403, (method, path)
    assert srv.req("PUT", VPATH, token="admin", json_body={})[0] == 405
    assert srv.req("POST", f"{VPATH}/1", token="admin", json_body={})[0] == 405
    assert srv.req("GET", f"{VPATH}/1/listing")[0] == 405
    assert srv.req("POST", f"{VPATH}/1/diff", token="admin", json_body={})[0] == 405
    assert srv.req("GET", f"{VPATH}/1/nope")[0] == 404
    assert srv.req("GET", f"{VPATH}/99")[0] == 404
    assert srv.req("DELETE", f"{VPATH}/99", token="admin")[0] == 404


def test_a_failed_draft_does_not_hold_its_name(vsrv):
    """워크플랜 §14-3 — 만들기가 실패한 행은 이름을 붙잡지 않는다. 스토어에는 아무것도 없고
    (`asc_version_id` 는 성공했을 때만 적힌다) 사람이 막힌 것을 고치고 **같은 이름으로 다시**
    누를 수 있어야 한다. 그 전에는 409 `version_exists` 라서 반드시 버리고 다시 만들어야 했다.
    실패한 행 자체는 목록에 남는다 — 사라지면 왜 실패했는지 읽을 수가 없다."""
    srv = vsrv
    first = srv.create({"ios_version": "1.1.1", "android_version": "1.0.1"})[1]
    srv.finish_job(first["job_id"], None, state=FAILED, exit_code=3)
    row = srv.store.get_version(first["id"])
    assert row["state"] == "failed" and row["asc_version_id"] is None
    status, again = srv.create({"ios_version": "1.1.1", "android_version": "1.0.1"})
    assert status == 202, again
    assert again["id"] != first["id"]
    # 실패한 행은 여전히 목록에 있고(닫힌 것이 아니다) 편집 · 버리기도 그대로다
    listed = srv.req("GET", VPATH)[1]["drafts"]
    assert sorted(d["id"] for d in listed) == [first["id"], again["id"]]
    assert [d["state"] for d in listed if d["id"] == first["id"]] == ["failed"]
    # 새 드래프트는 열려 있으므로 세 번째는 다시 막힌다
    assert code_of(srv.create({"ios_version": "1.1.1"})) == (409, "version_exists")


# ── 완료 훅 (AC-B5 · E4 · E5) ─────────────────────────────────────────────────


def test_the_create_job_hook_fills_asc_id_and_prefill_or_marks_the_row_failed(vsrv):
    """AC-B5 — 0 으로 끝나면 `editing` · `asc_version_id` · `prefill_json`; 1 이면 `failed` +
    `error`. E4 — exit 3 은 «already exists»; E5 — lost 는 `failed` 이고 asc id 가 없다."""
    srv = vsrv
    status, body = srv.create({"ios_version": "1.1.1", "android_version": "1.0.1"})
    vid, job_id = body["id"], body["job_id"]
    srv.finish_job(job_id, CREATE_FILES)
    row = srv.store.get_version(vid)
    assert row["state"] == "editing" and row["asc_version_id"] == "abc123"
    assert json.loads(row["prefill_json"]) == PREFILL_DOC and row["error"] is None
    doc = srv.version(vid)
    print("VERSION_JSON", json.dumps(doc, ensure_ascii=False)[:1500])
    assert doc["state"] == "editing" and doc["has_prefill"] is True
    assert doc["prefill"] == PREFILL_DOC and doc["edited"] is None
    assert doc["diff"] == {"fields": [], "screenshots": {"ios": "same", "android": "same"}}
    assert doc["release"]["build_name"] == "1.1.1" and doc["release"]["plan"] is None
    assert doc["release"]["jobs"][0]["role"] == "version"
    assert any("version: #1 1.1.1 created (job #1, asc abc123)" in m for m in srv.server_log)
    # exit 1 → failed
    status, body = srv.create({"ios_version": "1.1.2"})
    srv.finish_job(body["job_id"], None, state=FAILED, exit_code=1)
    row = srv.store.get_version(body["id"])
    assert row["state"] == "failed" and row["error"] == "job #2 failed (exit 1)"
    assert row["asc_version_id"] is None
    # E4 — 스토어에 같은 이름의 편집 중 버전 (exit 3, version.json 에 error 문장)
    status, body = srv.create({"ios_version": "1.1.3"})
    doc = {**VERSION_DOC, "ios": None, "error": "1.1.3 is PREPARE_FOR_SUBMISSION in ASC"}
    srv.finish_job(
        body["job_id"],
        {"version.json": json.dumps(doc).encode()},
        state=FAILED,
        exit_code=3,
    )
    row = srv.store.get_version(body["id"])
    assert row["state"] == "failed"
    assert row["error"] == (
        "job #3 failed (exit 3) — a version with that name already exists in the store — "
        "1.1.3 is PREPARE_FOR_SUBMISSION in ASC"
    )
    # E5 — lost
    status, body = srv.create({"ios_version": "1.1.4"})
    srv.finish_job(body["job_id"], None, state=LOST, exit_code=None)
    row = srv.store.get_version(body["id"])
    assert row["state"] == "failed" and row["error"] == "job #4 lost"
    assert row["asc_version_id"] is None
    # 성공했는데 version.json 이 없으면 있는 척 안 한다
    status, body = srv.create({"ios_version": "1.1.5"})
    srv.finish_job(body["job_id"], {"prefill.json": b"{}"})
    assert srv.store.get_version(body["id"])["state"] == "failed"
    # failed 행은 목록에 남는다(왜 실패했는지 읽을 수 있어야 한다). 이름은 붙잡지 않는다 —
    # 재시도는 같은 이름의 새 드래프트다(§14-3 · test_a_failed_draft_does_not_hold_its_name)
    listed = srv.req("GET", VPATH)[1]
    assert [d["state"] for d in listed["drafts"]] == [
        "failed",
        "failed",
        "failed",
        "failed",
        "editing",
    ]
    assert srv.create({"ios_version": "1.1.4"})[0] == 202


def test_the_hook_waits_for_a_bundle_still_in_transit(vsrv):
    """원격 워커의 잡은 끝난 뒤 묶음이 온다 — 훅은 `artifacts_changed` 때 산출물을 읽는다."""
    srv = vsrv
    status, body = srv.create({"ios_version": "1.1.1"})
    vid, job_id = body["id"], body["job_id"]
    srv.store._conn().execute(
        "UPDATE jobs SET state='running', started_at=?, lane=1 WHERE id=?",
        (NOW.timestamp(), job_id),
    )
    assert srv.store.finish(job_id, SUCCEEDED, now=NOW, exit_code=0)
    srv.store._conn().execute(
        "INSERT INTO job_artifacts (job_id, state) VALUES (?, ?)", (job_id, art.UPLOADING)
    )
    srv.app._on_job_change(job_id)
    assert srv.store.get_version(vid)["state"] == "creating"  # 아직 — 묶음이 오는 중
    payload = make_tar(CREATE_FILES)
    d = srv.cfg.data_dir / "artifacts" / str(job_id)
    d.mkdir(parents=True)
    (d / "bundle.tar").write_bytes(payload)
    manifest = {
        "files": [
            {"path": p, "size": len(b), "sha256": hashlib.sha256(b).hexdigest(), "mode": 420}
            for p, b in CREATE_FILES.items()
        ]
    }
    srv.store._conn().execute(
        "UPDATE job_artifacts SET state=?, manifest_json=? WHERE job_id=?",
        (art.READY, json.dumps(manifest), job_id),
    )
    srv.app.publish_artifacts(job_id, art.READY)
    row = srv.store.get_version(vid)
    assert row["state"] == "editing" and row["asc_version_id"] == "abc123"


def test_the_version_detail_runs_no_subprocess(vsrv):
    """워크플랜 §14-1 — 버전 페이지는 이 라우트를 5초마다 부른다. 그러니 한 번에 하위 프로세스가
    하나도 돌면 안 된다. 드라이버 `--status` 와 문안 명령 둘은 원래 있던 자기 라우트가 답하고,
    상세에는 `driver` · `listing` 이 없다. `release` 는 남는다 — 대장과 묶음 파일만 읽는다."""
    srv = vsrv
    srv.fetch()
    vid = editing_draft(srv)

    def never(*a, **kw):
        raise AssertionError(f"the version detail must not run commands: {a!r}")

    srv.app._run_listing = never
    srv.app.driver.status = never
    doc = srv.version(vid)
    assert "driver" not in doc and "listing" not in doc
    assert doc["release"]["build_name"] == "1.1.1"  # 여기에 필요한 것은 남아 있다
    assert doc["prefill"] == PREFILL_DOC and doc["diff"]["fields"] == []
    assert set(doc) == set(srv.req("GET", VPATH)[1]["drafts"][0]) | {
        "prefill",
        "edited",
        "diff",
        "release",
    }


# ── PUT …/listing · GET …/diff (AC-B6 · B7 · E9) ─────────────────────────────


def test_put_listing_merges_allowed_keys_and_refuses_the_rest(vsrv):
    """AC-B6 — 허용 키만 저장 · 모르는 키 400 · 16 KB 초과 400 · `last_edit_at` 갱신 · submitted 행
    409 `version_closed`. E9 — 4001자는 저장된다(카운터가 말한다)."""
    srv = vsrv
    vid = editing_draft(srv)
    assert srv.store.get_version(vid)["last_edit_at"] is None
    status, body = srv.put_listing(vid, {"ios": {"subtitle": "Sleep well"}})
    assert status == 200, body
    assert body["edited"] == {"ios": {"subtitle": "Sleep well"}}
    assert body["last_edit_at"] == "2026-09-17T12:00:00Z" and body["state"] == "editing"
    assert body["diff"]["fields"] == [
        {"platform": "ios", "key": "subtitle", "old": "Sleep better", "new": "Sleep well"}
    ]
    row = srv.store.get_version(vid)
    assert row["last_edit_at"] == NOW and json.loads(row["edited_json"]) == body["edited"]
    # 다음 PUT 은 그 키만 — 앞의 편집이 남는다
    long = "가" * 4001
    status, body = srv.put_listing(vid, {"android": {"full_description": long}})
    assert status == 200 and body["edited"] == {
        "ios": {"subtitle": "Sleep well"},
        "android": {"full_description": long},
    }
    assert srv.version(vid)["changed"] == 2 and srv.version(vid)["has_edits"] is True
    # null 은 키를 뺀다 · 빈 플랫폼은 사라진다
    status, body = srv.put_listing(vid, {"android": {"full_description": None}})
    assert status == 200 and body["edited"] == {"ios": {"subtitle": "Sleep well"}}
    status, body = srv.put_listing(vid, {"ios": None})
    assert status == 200 and body["edited"] is None
    assert srv.store.get_version(vid)["edited_json"] is None
    # 거절
    status, body = srv.put_listing(vid, {"ios": {"price": "1"}})
    assert (status, body["code"]) == (400, "listing_key") and "price" in body["error"]
    assert code_of(srv.put_listing(vid, {"web": {"title": "x"}})) == (400, "listing_key")
    status, body = srv.put_listing(vid, {"ios": {"description": "x" * (16 * 1024 + 1)}})
    assert (status, body["code"]) == (400, "listing_too_large") and body["key"] == "description"
    assert srv.put_listing(vid, {"ios": {"subtitle": 3}})[0] == 400
    assert srv.put_listing(vid, {"ios": "text"})[0] == 400
    assert srv.req("PUT", f"{VPATH}/{vid}/listing", token="admin", json_body=[])[0] == 400
    assert code_of(srv.put_listing(vid, {"ios": {"subtitle": "x"}}, token="alice")) == (
        403,
        "admin_required",
    )
    assert srv.store.get_version(vid)["edited_json"] is None
    # 큰 본문(64 KB 넘는 JSON)도 필드마다 16 KB 안이면 된다
    big = {"ios": {k: "y" * 15_000 for k in ("description", "keywords", "whats_new", "subtitle")}}
    assert srv.put_listing(vid, big)[0] == 200
    # 닫힌 행
    for state in ("submitted", "discarded"):
        srv.store.update_version(vid, state=state)
        status, body = srv.put_listing(vid, {"ios": {"subtitle": "x"}})
        assert (status, body["code"]) == (409, "version_closed") and body["state"] == state


def test_diff_lists_changed_fields_only_and_ignores_whitespace_and_line_endings(vsrv):
    """AC-B7 — 바뀐 필드만 `fields[]`, CRLF · 양끝 공백 차이는 «같음». E10 — 되돌리면 비어 있다."""
    srv = vsrv
    vid = editing_draft(srv)
    assert srv.req("GET", f"{VPATH}/{vid}/diff")[1] == {
        "fields": [],
        "screenshots": {"ios": "same", "android": "same"},
    }
    srv.put_listing(
        vid,
        {
            "ios": {"description": "A long description.\n", "keywords": "sleep,mood,calm"},
            "android": {"title": "  Dolomood  ", "whats_new": "Fixes"},
        },
    )
    body = srv.req("GET", f"{VPATH}/{vid}/diff")[1]
    print("DIFF_JSON", json.dumps(body))
    assert body["fields"] == [
        {"platform": "ios", "key": "keywords", "old": "sleep,mood", "new": "sleep,mood,calm"},
        {"platform": "android", "key": "whats_new", "old": "Bug fixes", "new": "Fixes"},
    ]
    srv.put_listing(vid, {"ios": {"keywords": "sleep,mood"}, "android": {"whats_new": "Bug fixes"}})
    assert srv.req("GET", f"{VPATH}/{vid}/diff")[1]["fields"] == []
    assert srv.version(vid)["changed"] == 0
    # prefill 이 없는 드래프트(E25) — 편집은 전부 «새 값», 스크린샷은 n/a
    srv.set_profile(PROFILE_NO_VERSION)
    status, body = srv.create({"android_version": "2.0.0"})
    srv.put_listing(body["id"], {"android": {"title": "New"}})
    assert srv.req("GET", f"{VPATH}/{body['id']}/diff")[1] == {
        "fields": [{"platform": "android", "key": "title", "old": None, "new": "New"}],
        "screenshots": {"ios": "n/a", "android": "n/a"},
    }


# ── version_id in plan · review · upload · start (AC-B8 · E10 · E11) ─────────


def test_plan_takes_the_build_name_from_the_version(vsrv):
    srv = vsrv
    vid = editing_draft(srv)
    status, body = srv.post("plan", {"version_id": vid})
    assert status == 202, body
    assert srv.store.get_job(body["job_id"]).inputs == {"build_name": "1.1.1"}
    status, body = srv.post("plan", {"version_id": vid, "build_name": "1.1.1"})
    assert status == 202
    status, body = srv.post("plan", {"version_id": vid, "build_name": "1.0.9"})
    assert (status, body["code"]) == (400, "build_name_mismatch") and body["build_name"] == "1.1.1"
    assert srv.post("plan", {"version_id": "1"})[0] == 400
    assert srv.post("plan", {"version_id": 0})[0] == 400
    assert code_of(srv.post("plan", {"version_id": 99})) == (404, "version_not_found")
    srv.store.update_version(vid, state="discarded")
    assert code_of(srv.post("plan", {"version_id": vid})) == (409, "version_closed")
    # plan 은 행의 상태를 바꾸지 않는다(심사 잡 · 회차만 running)
    android_only = srv.create({"android_version": "3.0.0"})[1]["id"]
    srv.store.update_version(android_only, state="editing")
    status, body = srv.post("plan", {"version_id": android_only})
    assert status == 202 and srv.store.get_job(body["job_id"]).inputs == {"build_name": "3.0.0"}
    assert srv.store.get_version(android_only)["state"] == "editing"


def test_review_forwards_the_edited_listing_and_tracks_the_row_state(vsrv):
    """AC-B8 — 편집이 있으면 잡 입력 `listing_json` 에 JSON(edited ⊕ prefill); 편집이 없으면 입력을
    보내지 않는다; 프리셋에 입력이 없으면 409 `listing_json_unsupported`(E11); `build_name`
    불일치 400. 행은 `running` 이었다가 잡이 끝나면 `editing`, submit 이 성공하면 `submitted`."""
    srv = vsrv
    vid = editing_draft(srv)
    status, body = srv.post("review", {"version_id": vid, "mode": "plan"})
    assert status == 202, body
    job = srv.store.get_job(body["job_id"])
    assert job.preset == "release-review-listing" and job.inputs["build_name"] == "1.1.1"
    assert job.inputs["listing_json"] == ""  # 편집 없음 → 프리셋 기본값
    assert job.inputs["build_name_android"] == "1.0.1"  # §11 — 두 이름이 다르다
    row = srv.store.get_version(vid)
    assert row["state"] == "running" and row["review_job_id"] == job.id
    assert code_of(srv.discard(vid)) == (409, "version_running")  # E15
    srv.finish_job(job.id, {"review-plan.json": json.dumps(REVIEW_PLAN_DOC).encode()})
    assert srv.store.get_version(vid)["state"] == "editing"
    # 편집 → listing_json (edited ⊕ prefill, 문안 키만)
    srv.put_listing(vid, {"ios": {"subtitle": "Sleep well"}, "android": {"whats_new": "Fixes"}})
    status, body = srv.post("review", {"version_id": vid, "mode": "plan", "platform": "ios"})
    assert status == 202, body
    sent = json.loads(srv.store.get_job(body["job_id"]).inputs["listing_json"])
    print("LISTING_JSON", json.dumps(sent, ensure_ascii=False))
    assert sent == {
        "ios": {
            "subtitle": "Sleep well",
            "promotional_text": "",
            "description": "A long description.\r\n",
            "keywords": "sleep,mood",
            "support_url": "https://example.com/support",
            "marketing_url": "",
            "whats_new": "Bug fixes",
        },
        "android": {
            "title": "Dolomood",
            "short_description": "Track your mood",
            "full_description": "Full text",
            "whats_new": "Fixes",
        },
    }
    srv.finish_job(body["job_id"], None, state=FAILED, exit_code=1)
    assert srv.store.get_version(vid)["state"] == "editing"
    # E10 — 되돌려 prefill 과 같아지면 보내지 않는다
    srv.put_listing(
        vid, {"ios": {"subtitle": "Sleep better"}, "android": {"whats_new": "Bug fixes"}}
    )
    status, body = srv.post("review", {"version_id": vid})
    assert status == 202 and srv.store.get_job(body["job_id"]).inputs["listing_json"] == ""
    srv.finish_job(body["job_id"], None)
    # E11 — 프리셋에 listing_json 이 없는데 편집이 있다
    srv.put_listing(vid, {"ios": {"subtitle": "Sleep well"}})
    srv.set_profile({**PROFILE_V, "presets": {**PROFILE_V["presets"], "review": "release-review"}})
    status, body = srv.post("review", {"version_id": vid})
    assert (status, body["code"]) == (409, "listing_json_unsupported")
    assert body["preset"] == "release-review" and srv.store.get_version(vid)["state"] == "editing"
    srv.set_profile(PROFILE_V)
    assert code_of(srv.post("review", {"version_id": vid, "build_name": "1.0.1"})) == (
        400,
        "build_name_mismatch",
    )
    # submit 성공 → submitted · history 에 실린다
    srv.plan_job({**PLAN_DOC, "build_name": "1.1.1"})
    review_plan = {**REVIEW_PLAN_DOC, "build_name": "1.1.1"}
    srv.role_job("release-review-listing", {"review-plan.json": json.dumps(review_plan).encode()})
    submit = {
        "version_id": vid,
        "mode": "submit",
        "confirm_build_number": "181",
        "play_managed_publishing": "confirmed-on",
    }
    assert code_of(srv.post("review", submit, token="alice")) == (403, "admin_required")
    status, body = srv.post("review", submit, token="admin")
    assert status == 202, body
    job = srv.store.get_job(body["job_id"])
    assert job.inputs["mode"] == "submit" and json.loads(job.inputs["listing_json"])["ios"]
    assert srv.store.get_version(vid)["state"] == "running"
    srv.finish_job(job.id, {"review.json": b'{"schema": 1, "status": "submitted"}'})
    row = srv.store.get_version(vid)
    assert row["state"] == "submitted" and row["review_job_id"] == job.id
    listed = srv.req("GET", VPATH)[1]
    assert listed["drafts"] == []
    assert listed["history"] == [
        {
            "id": vid,
            "ios": "1.1.1",
            "android": "1.0.1",
            "submitted_at": "2026-09-17T12:00:00Z",
            "review_job_id": job.id,
        }
    ]
    # E14 — 제출된 버전은 지울 수 없고, 편집도 닫힌다
    assert code_of(srv.discard(vid)) == (409, "version_closed")
    assert code_of(srv.put_listing(vid, {"ios": {"subtitle": "x"}})) == (409, "version_closed")
    assert code_of(srv.post("review", {"version_id": vid})) == (409, "version_closed")


def test_upload_forwards_the_edited_listing_too(vsrv):
    """두 스토어가 한 이름(1.1.1)을 쓰는 드래프트 — §11 은 끼어들지 않고 입력은 오늘 그대로다."""
    srv = vsrv
    vid = editing_draft(srv, ios="1.1.1", android="1.1.1")
    status, body = srv.post("upload", {"version_id": vid, "platform": "android"})
    assert status == 202, body
    job = srv.store.get_job(body["job_id"])
    assert job.inputs["build_name"] == "1.1.1" and "listing_json" not in job.inputs
    assert "build_name_android" not in job.inputs  # 이름이 같다 → 두 번째 이름은 없다
    srv.put_listing(vid, {"android": {"title": "New name"}})
    status, body = srv.post("upload", {"version_id": vid})
    assert (status, body["code"]) == (409, "listing_json_unsupported")
    srv.cfg.presets = (parse_preset(upload_preset(LISTING_JSON_INPUT)), *srv.cfg.presets)
    srv.set_profile(
        {**PROFILE_V, "presets": {**PROFILE_V["presets"], "upload": "release-upload-listing"}}
    )
    status, body = srv.post("upload", {"version_id": vid})
    assert status == 202, body
    sent = json.loads(srv.store.get_job(body["job_id"]).inputs["listing_json"])
    assert sent["android"]["title"] == "New name" and sent["ios"]["subtitle"] == "Sleep better"
    # rehearsal 은 스토어에 아무것도 쓰지 않는다 — 행은 그대로 열려 있다(§14-2)
    row = srv.store.get_version(vid)
    assert row["state"] == "editing" and row["upload_job_id"] is None
    assert srv.discard(vid)[0] == 202  # 그래서 «버리기» 도 열려 있다


def test_an_upload_holds_the_row_until_it_ends_and_a_rehearsal_does_not(vsrv):
    """워크플랜 §14-2 · §2.2 (d) — `mode=upload` 는 스토어에 바이너리를 올린다. 그 동안 행은
    `running` 이고 «버리기» 는 409 `version_running` 이다(E15). 예전에는 review · start 만 행을
    붙잡아서, 셋 중 가장 되돌리기 어려운 것만 열려 있었다 — 올리는 중에 버리면 App Store 버전을
    지우는 잡까지 나갔다. 잡이 끝나면 어떻게 끝났든 `editing` 으로 돌아온다."""
    srv = vsrv
    vid = editing_draft(srv, ios="1.1.1", android="1.1.1")
    srv.plan_job({**PLAN_DOC, "build_name": "1.1.1"})
    up = {"version_id": vid, "mode": "upload", "confirm_build_number": "181"}
    status, body = srv.post("upload", up, token="admin")
    assert status == 202, body
    job = srv.store.get_job(body["job_id"])
    assert job.inputs["mode"] == "upload" and job.inputs["build_name"] == "1.1.1"
    row = srv.store.get_version(vid)
    assert row["state"] == "running" and row["upload_job_id"] == job.id
    status, refused = srv.discard(vid)
    assert (status, refused["code"]) == (409, "version_running")
    assert refused["upload_job_id"] == job.id and refused["review_job_id"] is None
    assert srv.store.list_jobs_by_preset(["release-version"], 20)[0].inputs["mode"] == "create"
    assert code_of(srv.put_listing(vid, {"ios": {"subtitle": "x"}})) == (200, None)  # 편집은 열려
    srv.put_listing(vid, {"ios": {"subtitle": None}})  # 되돌린다(이 프리셋은 그 입력을 모른다)
    srv.finish_job(job.id, {"out/upload.json": b'{"schema": 1, "build": 181}'})
    assert srv.store.get_version(vid)["state"] == "editing"
    assert any(f"upload job #{job.id} succeeded → editing" in m for m in srv.server_log)
    # 실패해도 돌아온다 — 행이 말하는 것은 «되돌릴 수 없는 일이 도는 중인가» 뿐이다
    status, body = srv.post("upload", up, token="admin")
    assert status == 202 and srv.store.get_version(vid)["state"] == "running"
    srv.finish_job(body["job_id"], None, state=FAILED, exit_code=1)
    assert srv.store.get_version(vid)["state"] == "editing"
    # lost 도 같다
    status, body = srv.post("upload", up, token="admin")
    srv.finish_job(body["job_id"], None, state=LOST, exit_code=None)
    assert srv.store.get_version(vid)["state"] == "editing"
    # rehearsal 은 붙잡지 않는다
    status, body = srv.post("upload", {"version_id": vid, "mode": "rehearsal"})
    assert status == 202, body
    row = srv.store.get_version(vid)
    assert row["state"] == "editing" and row["upload_job_id"] != body["job_id"]
    assert srv.discard(vid)[0] == 202


def test_restart_recovery_settles_a_row_left_running_by_an_upload(vsrv):
    """AC-B11 · §14-2 — 서버가 꺼진 동안 올리기가 끝났으면 기동 복구가 행을 `editing` 으로
    되돌린다. 아직 도는 중이면 붙잡은 그대로다 — 재시작이 «버리기» 를 열어 주면 안 된다."""
    srv = vsrv
    srv.plan_job({**PLAN_DOC, "build_name": "1.1.1"})
    up = {"mode": "upload", "confirm_build_number": "181"}
    done = editing_draft(srv, ios="1.1.1", android="1.1.1")
    status, body = srv.post("upload", {**up, "version_id": done}, token="admin")
    assert status == 202, body
    srv.finish_quietly(body["job_id"], {"out/upload.json": b"{}"})
    assert srv.store.get_version(done)["state"] == "running"
    srv.plan_job({**PLAN_DOC, "build_name": "1.2.1"})
    running = editing_draft(srv, ios="1.2.1", android="1.2.1")
    status, body = srv.post("upload", {**up, "version_id": running}, token="admin")
    assert status == 202, body

    srv.app.recover_versions_on_start()

    assert srv.store.get_version(done)["state"] == "editing"
    assert srv.store.get_version(running)["state"] == "running"  # 아직 큐에 있다
    assert code_of(srv.discard(running)) == (409, "version_running")


def live_round(srv: VersionServer, build_name: str) -> int:
    """도는 중인 회차 하나 — 프로세스를 띄우지 않고 이 프로세스의 pid 를 적는다(`pid_alive` 가
    참이라 `reconcile` 이 닫지 않는다). 드라이버 스텁은 순식간에 끝나 «도는 중» 을 못 만든다."""
    rid = srv.store.create_release(
        repo="app",
        build_name=build_name,
        kind="start",
        started_by="macmini-admin",
        now=NOW,
        log_path=str(srv.cfg.data_dir / "driver" / "app" / f"{build_name}.log"),
    )
    srv.store.set_release_started(rid, pid=os.getpid(), token_name=f"store-driver:app:{rid}")
    return rid


def test_a_job_that_ends_during_a_round_leaves_the_row_running(vsrv):
    """워크플랜 §14-2 — 행을 붙잡는 것은 셋이다: 심사 잡 · 올리기 잡 · 드라이버 회차. 손으로 낸
    잡이 회차보다 **먼저** 끝나도 행은 `running` 그대로여야 한다. 그러지 않으면 회차가 아직
    스토어에 올리는 중인 버전을 버릴 수 있고, 그것이 이 PR 이 닫은 바로 그 문이다. 내려놓는 것은
    마지막에 끝나는 쪽이고, 잡 훅 · 회차 훅 · 기동 복구가 같은 판정(`_version_busy`)을 쓴다."""
    srv = vsrv
    vid = editing_draft(srv, ios="1.1.1", android="1.1.1")
    srv.plan_job({**PLAN_DOC, "build_name": "1.1.1"})
    rid = live_round(srv, "1.1.1")
    srv.app._version_link_release(vid, rid)  # start 가 회차를 행에 붙이는 그 자리
    assert srv.store.get_version(vid)["state"] == "running"
    # 올리기 잡이 회차보다 먼저 끝난다
    up = {"version_id": vid, "mode": "upload", "confirm_build_number": "181"}
    status, body = srv.post("upload", up, token="admin")
    assert status == 202, body
    srv.finish_job(body["job_id"], {"out/upload.json": b'{"schema": 1}'})
    assert srv.store.get_version(vid)["state"] == "running"
    assert code_of(srv.discard(vid)) == (409, "version_running")
    assert any(f"round #{rid} is still running" in m for m in srv.server_log)
    # 심사 잡도 같다
    status, body = srv.post("review", {"version_id": vid, "mode": "plan"})
    assert status == 202, body
    srv.finish_job(body["job_id"], {"review-plan.json": json.dumps(REVIEW_PLAN_DOC).encode()})
    assert srv.store.get_version(vid)["state"] == "running"
    assert code_of(srv.discard(vid)) == (409, "version_running")
    # 재시작도 회차를 존중한다 — 붙잡은 것이 살아 있으면 그대로다
    srv.app.recover_versions_on_start()
    assert srv.store.get_version(vid)["state"] == "running"
    # 회차가 끝나면 그때 내려온다
    assert srv.store.finish_release(rid, 0, NOW)
    srv.app._driver_exited(srv.store.get_release(rid))
    assert srv.store.get_version(vid)["state"] == "editing"
    assert any(f"round #{rid} ended → editing" in m for m in srv.server_log)
    assert srv.discard(vid)[0] == 202


def test_a_round_that_ends_first_waits_for_the_job_that_is_still_running(vsrv):
    """반대 차례 — 회차가 먼저 끝나고 손으로 낸 올리기 잡이 아직 돌 때. 회차 훅도 같은 판정을
    쓰므로 행은 `running` 이고, 잡이 끝날 때 내려온다. 사라진 회차(프로세스는 죽었는데 대장이
    열려 있는 것)는 `reconcile` 이 먼저 닫으니 행을 영영 붙잡지 못한다 — 기동 복구로 확인한다."""
    srv = vsrv
    vid = editing_draft(srv, ios="1.1.1", android="1.1.1")
    srv.plan_job({**PLAN_DOC, "build_name": "1.1.1"})
    rid = live_round(srv, "1.1.1")
    srv.app._version_link_release(vid, rid)
    up = {"version_id": vid, "mode": "upload", "confirm_build_number": "181"}
    status, body = srv.post("upload", up, token="admin")
    assert status == 202, body
    assert srv.store.finish_release(rid, 0, NOW)
    srv.app._driver_exited(srv.store.get_release(rid))
    row = srv.store.get_version(vid)
    assert row["state"] == "running" and row["upload_job_id"] == body["job_id"]
    assert code_of(srv.discard(vid)) == (409, "version_running")
    assert any(f"job #{body['job_id']} is still running" in m for m in srv.server_log)
    srv.app.recover_versions_on_start()  # 회차는 갔지만 잡이 남았다
    assert srv.store.get_version(vid)["state"] == "running"
    srv.finish_job(body["job_id"], None, state=FAILED, exit_code=1)
    assert srv.store.get_version(vid)["state"] == "editing"


def test_two_different_store_version_names_travel_as_build_name_and_build_name_android(vsrv):
    """워크플랜 §11 · 계약 §2 「Two store version names」 — 한 회차가 App Store 와 Play 에 서로
    다른 이름으로 나갈 때 `build_name` 은 대표 이름(iOS)이고 Android 이름은 `build_name_android`
    로 함께 간다. 받는 역할은 upload · review 뿐이다."""
    srv = vsrv
    srv.cfg.presets = (
        parse_preset(upload_preset(LISTING_JSON_INPUT, BUILD_NAME_ANDROID_INPUT)),
        *srv.cfg.presets,
    )
    srv.set_profile(
        {**PROFILE_V, "presets": {**PROFILE_V["presets"], "upload": "release-upload-listing"}}
    )
    vid = editing_draft(srv, ios="1.1.1", android="1.0.1")
    for action in ("review", "upload"):
        status, body = srv.post(action, {"version_id": vid})
        assert status == 202, body
        job = srv.store.get_job(body["job_id"])
        assert (job.inputs["build_name"], job.inputs["build_name_android"]) == ("1.1.1", "1.0.1")
        srv.finish_job(job.id, None)
    # plan 은 스토어를 읽을 뿐이라 이름 하나로 충분하다 — 입력도 거절도 없다(§11)
    status, body = srv.post("plan", {"version_id": vid})
    assert status == 202 and "build_name_android" not in srv.store.get_job(body["job_id"]).inputs
    assert srv.store.get_job(body["job_id"]).inputs["build_name"] == "1.1.1"


def test_one_name_for_both_stores_sends_exactly_what_it_sends_today(vsrv):
    """§11 — 두 이름이 같거나 한 스토어만 만드는 버전이면 `build_name` 하나다. 프리셋이 그 입력을
    선언해 두었어도 rcm 은 값을 보내지 않는다(기본값 빈 문자열 그대로)."""
    srv = vsrv
    same = editing_draft(srv, ios="1.1.1", android="1.1.1")
    status, body = srv.post("review", {"version_id": same})
    assert status == 202, body
    inputs = srv.store.get_job(body["job_id"]).inputs
    assert inputs["build_name"] == "1.1.1" and inputs["build_name_android"] == ""
    srv.finish_job(body["job_id"], None)
    only_ios = editing_draft(srv, ios="1.2.0", android=None)
    status, body = srv.post("review", {"version_id": only_ios})
    assert status == 202, body
    inputs = srv.store.get_job(body["job_id"]).inputs
    assert inputs["build_name"] == "1.2.0" and inputs["build_name_android"] == ""
    srv.finish_job(body["job_id"], None)
    only_android = editing_draft(srv, ios=None, android="1.0.1")
    status, body = srv.post("review", {"version_id": only_android})
    assert status == 202, body
    inputs = srv.store.get_job(body["job_id"]).inputs
    assert inputs["build_name"] == "1.0.1" and inputs["build_name_android"] == ""


def test_a_preset_that_does_not_know_build_name_android_refuses_the_split_names(vsrv):
    """§11 — 두 이름이 다른데 프리셋에 `build_name_android` 가 없으면 409
    `split_version_unsupported`. 한쪽 이름으로 조용히 빌드하지 않는다."""
    srv = vsrv
    vid = editing_draft(srv, ios="1.1.1", android="1.0.1")
    # review 프리셋을 그 입력이 없는 옛것으로 — listing_json 도 없지만 편집이 없으니 §11 이 먼저다
    srv.set_profile({**PROFILE_V, "presets": {**PROFILE_V["presets"], "review": "release-review"}})
    status, body = srv.post("review", {"version_id": vid})
    assert (status, body["code"]) == (409, "split_version_unsupported")
    assert body["error_code"] == "split_version_unsupported"
    assert (body["preset"], body["ios_version"], body["android_version"]) == (
        "release-review",
        "1.1.1",
        "1.0.1",
    )
    assert "re-run the store-connect skill" in body["error"]
    assert srv.store.get_version(vid)["state"] == "editing"  # 잡도 상태 변화도 없다
    # upload 도 같다(프로파일의 기본 `release-upload` 는 그 입력을 모른다)
    assert code_of(srv.post("upload", {"version_id": vid})) == (409, "split_version_unsupported")
    # 버전을 붙이지 않은 제출은 옛길 그대로 — 사람이 친 build_name 하나다
    assert srv.post("review", {"build_name": "1.1.1"})[0] == 202


def test_start_passes_version_id_to_the_driver_and_the_round_marks_the_row_running(vsrv):
    """(c) `--version-id` 전달 · `release_id` 링크 · (d) 회차가 끝나면 다시 `editing`."""
    srv = vsrv
    srv.fetch()
    vid = editing_draft(srv)
    (srv.secrets_root / "hold").touch()
    status, body = srv.post("start", {"version_id": vid, "dry_run": True}, token="admin")
    assert status == 202, body
    assert body["build_name"] == "1.1.1"
    row = srv.store.get_version(vid)
    assert row["state"] == "running" and row["release_id"] == body["release_id"]
    assert code_of(srv.discard(vid)) == (409, "version_running")  # E15
    status, again = srv.post("start", {"version_id": vid}, token="admin")
    assert (status, again["code"]) == (409, "release_running")
    (srv.secrets_root / "hold").unlink()
    rel = wait_run(srv, body["release_id"])
    log = Path(rel["log_path"]).read_text()
    assert "driver --build-name 1.1.1 --version-id 1 --dry-run" in log
    deadline = 200
    while srv.store.get_version(vid)["state"] == "running" and deadline:
        deadline -= 1
        time.sleep(0.05)
    assert srv.store.get_version(vid)["state"] == "editing"
    assert any("round #1 ended → editing" in m for m in srv.server_log)
    # 사람 차례의 confirm 은 같은 버전으로 잇는다 — 다시 running, 끝나면 editing
    status, body = srv.post(
        "confirm", {"build_name": "1.1.1", "build_number": "181"}, token="admin"
    )
    assert status == 202, body
    assert srv.store.get_version(vid)["release_id"] == body["release_id"]
    rel = wait_run(srv, body["release_id"])
    assert (
        "--version-id 1 --dry-run --confirm-build-number 181" in Path(rel["log_path"]).read_text()
    )
    assert code_of(
        srv.post("start", {"version_id": vid, "build_name": "1.0.1"}, token="admin")
    ) == (
        400,
        "build_name_mismatch",
    )


def test_a_round_started_with_build_number_auto_keeps_the_version_on_the_confirm_run(vsrv):
    srv = vsrv
    srv.fetch()
    vid = editing_draft(srv)
    status, body = srv.post(
        "start", {"version_id": vid, "dry_run": True, "build_number": "auto"}, token="admin"
    )
    assert status == 202, body
    wait_run(srv, 1)
    deadline = 100
    while srv.store.count_releases() < 2 and deadline:
        deadline -= 1
        time.sleep(0.05)
    second = wait_run(srv, 2)
    assert second["kind"] == "confirm"
    # 이어 달린 회차의 명령줄. 로그는 detached 프로세스가 쓰므로 나타날 때까지 기다린다.
    log, wanted = Path(second["log_path"]), "--version-id 1 --dry-run --confirm-build-number 181"
    deadline = 200
    while wanted not in log.read_text() and deadline:
        deadline -= 1
        time.sleep(0.05)
    assert wanted in log.read_text()
    deadline = 200
    while srv.store.get_version(vid)["state"] == "running" and deadline:
        deadline -= 1
        time.sleep(0.05)
    row = srv.store.get_version(vid)
    assert row["state"] == "editing" and row["release_id"] == 2


def test_the_driver_view_says_which_stages_the_driver_knows(vsrv):
    """워크플랜 §13-2 — `--status` 의 `stages:` 줄이 능력 신호다. 요즘 드라이버는 `V` 를 말한다."""
    srv = vsrv
    srv.fetch()
    doc = srv.req("GET", "/api/repos/app/release/driver")[1]
    assert doc["stages"] == ["V", "S0", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8"]
    assert doc["knows_version_stage"] is True
    # 버전 페이지는 이 라우트를 자기 박자로 부른다 — 상세에는 `driver` 가 없다(§14-1)
    assert "driver" not in srv.version(editing_draft(srv))


def test_a_driver_that_does_not_know_the_v_stage_is_called_the_old_way(vsrv):
    """워크플랜 §13-2 · §8 E16 — `--status` 에 `stages:` 줄이 없으면(옛 드라이버) 서버는
    `--version-id` 를 **주지 않는다**. 이름은 이미 행에서 정해졌으니 회차는 옛길 그대로 돈다 —
    설명 없는 exit 2 로 죽지 않는다. 화면이 말할 수 있게 보기에도 그 사실이 남는다."""
    srv = vsrv
    srv.set_profile({**PROFILE_V, "driver": "scripts/old_driver.sh"})
    srv.fetch()
    vid = editing_draft(srv)
    doc = srv.req("GET", "/api/repos/app/release/driver")[1]
    assert doc["status"] == ["driver --status", "server=none token=", "stage: S1 planned"]
    assert doc["stages"] is None and doc["knows_version_stage"] is False
    status, body = srv.post("start", {"version_id": vid, "dry_run": True}, token="admin")
    assert status == 202, body
    assert body["build_name"] == "1.1.1"  # 이름은 그대로 버전 행에서 왔다
    assert srv.store.get_version(vid)["release_id"] == body["release_id"]
    rel = wait_run(srv, body["release_id"])
    log = Path(rel["log_path"]).read_text()
    assert "driver --build-name 1.1.1 --dry-run" in log and "--version-id" not in log
    # 옛길을 끝까지 달렸다: «번호가 필요하다» 는 그 exit 2 이지 `unknown argument` 가 아니다
    assert rel["exit_code"] == 2 and "plan: N = 181" in log and "unknown argument" not in log
    assert any("does not advertise the V stage" in m for m in srv.server_log)


# ── DELETE …/versions/<id> (AC-B9 · E14 · E15) ────────────────────────────────


def test_discard_runs_the_delete_job_when_the_store_has_the_version(vsrv):
    """AC-B9 — asc id 있음 → delete 잡 202 → 0 이면 `discarded`; 잡 실패 → 행은 editing 유지 +
    error; 없음 → 즉시 200 discarded."""
    srv = vsrv
    vid = editing_draft(srv)
    status, body = srv.discard(vid, token="alice")
    assert (status, body["code"]) == (403, "admin_required")
    status, body = srv.discard(vid)
    assert status == 202, body
    assert body == {"id": vid, "job_id": body["job_id"], "state": "editing"}
    job = srv.store.get_job(body["job_id"])
    assert job.preset == "release-version" and job.inputs == {
        "mode": "delete",
        "asc_version_id": "abc123",
        "ios_version": "1.1.1",
        "android_version": "1.0.1",
    }
    row = srv.store.get_version(vid)
    assert row["state"] == "editing" and row["delete_job_id"] == job.id
    # 도는 동안 다시 누르면 같은 잡
    assert srv.discard(vid) == (202, {"id": vid, "job_id": job.id, "state": "editing"})
    # 잡 실패 → 행은 editing 그대로 + error (E12 의 서버 절반)
    srv.finish_job(
        job.id, {"version.json": b'{"error": "ASC said 409"}'}, state=FAILED, exit_code=4
    )
    row = srv.store.get_version(vid)
    assert row["state"] == "editing"
    assert row["error"] == (
        f"job #{job.id} failed (exit 4) — cannot be deleted — the version was submitted in "
        "the store — ASC said 409"
    )
    assert row["asc_version_id"] == "abc123"
    # 다시 시도 → 새 잡, 0 으로 끝나면 discarded
    status, body = srv.discard(vid)
    assert status == 202 and body["job_id"] != job.id
    assert srv.store.get_version(vid)["error"] is None
    srv.finish_job(body["job_id"], {"version.json": b'{"mode": "delete"}'})
    row = srv.store.get_version(vid)
    assert row["state"] == "discarded" and row["error"] is None
    assert srv.discard(vid) == (200, {"id": vid, "job_id": None, "state": "discarded"})
    assert srv.req("GET", VPATH)[1]["drafts"] == []
    # asc id 없음 → 즉시 discarded
    srv.set_profile(PROFILE_NO_VERSION)
    quick = srv.create({"ios_version": "1.1.2"})[1]["id"]
    assert srv.discard(quick) == (200, {"id": quick, "job_id": None, "state": "discarded"})
    srv.set_profile(PROFILE_V)
    # 프리셋은 있지만 asc id 가 없는 failed 행도 즉시
    status, body = srv.create({"ios_version": "1.1.3"})
    srv.finish_job(body["job_id"], None, state=FAILED, exit_code=1)
    assert srv.discard(body["id"])[0] == 200
    assert srv.store.get_version(body["id"])["state"] == "discarded"
    # creating 인데 잡이 아직 도는 중이면 409
    status, body = srv.create({"ios_version": "1.1.4"})
    status, refused = srv.discard(body["id"])
    assert (status, refused["code"]) == (409, "version_running")
    assert refused["job_id"] == body["job_id"]
    assert srv.store.list_jobs_by_preset(["release-version"], 20)[0].inputs["mode"] == "create"


def test_discard_with_the_gate_closed_needs_no_job_or_refuses_the_job(
    tmp_path, remote, monkeypatch
):
    """관문이 닫혀 있으면 delete 잡을 낼 수 없다(409 `setup_incomplete`); 스토어에 없는 버전은
    잡 없이 버려지니 관문과 무관하다."""
    s = VersionServer(tmp_path, remote)
    try:
        vid = s.store.create_version(
            repo="app",
            ios_version="1.1.1",
            android_version=None,
            state="editing",
            created_by="macmini-admin",
            now=NOW,
            expires_at=NOW + timedelta(hours=24),
        )
        s.store.update_version(vid, asc_version_id="abc123")
        assert s.req("GET", "/api/repos/app")[1]["setup"]["complete"] is False
        assert code_of(s.discard(vid)) == (409, "setup_incomplete")
        assert s.store.get_version(vid)["state"] == "editing"
        s.store.update_version(vid, asc_version_id=None)
        assert s.discard(vid)[0] == 200
        assert s.store.get_version(vid)["state"] == "discarded"
    finally:
        s.close()


# ── 재시작 복구 (AC-B11 · E6) ─────────────────────────────────────────────────


def test_restart_recovery_reapplies_the_hook_and_settles_running_rows(vsrv):
    """AC-B11 · E6 — `creating` + 끝난 잡 → 훅 재적용; `running` + 회차 없음 → `editing`;
    `running` + 끝난 심사 submit → `submitted`; 도는 잡 · 회차는 그대로."""
    srv = vsrv
    # creating + 서버가 꺼진 동안 끝난 create 잡
    a = srv.create({"ios_version": "1.1.1"})[1]
    srv.finish_quietly(a["job_id"], CREATE_FILES)
    assert srv.store.get_version(a["id"])["state"] == "creating"
    # creating + 아직 큐에 있는 잡 → 그대로
    b = srv.create({"ios_version": "1.1.2"})[1]
    # creating + 잡 행이 사라짐 → failed
    c = srv.create({"ios_version": "1.1.3"})[1]
    srv.store._conn().execute("DELETE FROM jobs WHERE id=?", (c["job_id"],))
    # running + 회차도 심사 잡도 없음 → editing
    d = srv.create({"ios_version": "1.1.4"})[1]["id"]
    srv.store.update_version(d, state="running", release_id=77)
    # running + 서버가 꺼진 동안 끝난 심사 submit 잡 → submitted
    e = srv.create({"ios_version": "1.1.5"})[1]["id"]
    srv.store.update_version(e, state="editing")
    status, body = srv.post("review", {"version_id": e, "mode": "plan"})
    assert status == 202
    srv.finish_quietly(body["job_id"], {"review.json": b"{}"})
    srv.store._conn().execute(
        "UPDATE jobs SET inputs_json=? WHERE id=?",
        (
            json.dumps({**srv.store.get_job(body["job_id"]).inputs, "mode": "submit"}),
            body["job_id"],
        ),
    )
    assert srv.store.get_version(e)["state"] == "running"
    # running + 심사 잡이 아직 도는 중 → 그대로
    f = srv.create({"ios_version": "1.1.6"})[1]["id"]
    srv.store.update_version(f, state="editing")
    status, body = srv.post("review", {"version_id": f})
    assert srv.store.get_version(f)["state"] == "running"
    # delete 잡이 꺼진 동안 0 으로 끝남 → discarded
    g = editing_draft(srv, "1.1.7", "1.0.7")
    status, body = srv.discard(g)
    srv.finish_quietly(body["job_id"], {"version.json": b"{}"})
    assert srv.store.get_version(g)["state"] == "editing"

    srv.app.recover_versions_on_start()

    assert srv.store.get_version(a["id"])["state"] == "editing"
    assert srv.store.get_version(a["id"])["asc_version_id"] == "abc123"
    assert srv.store.get_version(b["id"])["state"] == "creating"
    row = srv.store.get_version(c["id"])
    assert row["state"] == "failed" and row["error"] == f"create job #{c['job_id']} is gone"
    assert srv.store.get_version(d)["state"] == "editing"
    assert srv.store.get_version(e)["state"] == "submitted"
    assert srv.store.get_version(f)["state"] == "running"
    assert srv.store.get_version(g)["state"] == "discarded"
    assert any("#4 was running with nothing running → editing" in m for m in srv.server_log)
    # 두 번 돌려도 같다
    srv.app.recover_versions_on_start()
    assert srv.store.get_version(a["id"])["state"] == "editing"
    assert srv.store.get_version(f)["state"] == "running"


def test_a_lost_create_job_on_restart_marks_the_draft_failed(tmp_path, remote, monkeypatch):
    """`App.start()` 의 recover_on_start 가 도는 잡을 lost 로 닫으면 그 훅이 드래프트를 failed
    로."""
    s = VersionServer(tmp_path, remote)
    try:
        s.open_gate(monkeypatch)
        body = s.create({"ios_version": "1.1.1"})[1]
        s.store._conn().execute(
            "UPDATE jobs SET state='running', started_at=?, lane=1 WHERE id=?",
            (NOW.timestamp(), body["job_id"]),
        )
        s.app.start()
        row = s.store.get_version(body["id"])
        assert row["state"] == "failed" and row["error"] == f"job #{body['job_id']} lost"
    finally:
        s.close()


def test_the_versions_table_is_read_by_sqlite_as_documented(vsrv):
    """워크플랜 §2.1 의 열 집합 그대로(웹 · 청소기 · CLI 가 같은 이름을 쓴다)."""
    srv = vsrv
    editing_draft(srv)
    c = sqlite3.connect(srv.cfg.data_dir / "rcm.sqlite3")
    try:
        row = c.execute("SELECT state, asc_version_id, expiry_warned FROM versions").fetchone()
        assert row == ("editing", "abc123", 0)
    finally:
        c.close()


#: 문서가 라우트를 말하지 않으면 여기서 빨개진다(집안 규칙 — 기능은 같은 PR 에서 문서가 된다).
@pytest.mark.parametrize(
    "path, pattern",
    [
        ("docs/configuration.md", r"`GET …/release/versions`"),
        ("docs/configuration.md", r"`POST …/release/versions`"),
        ("docs/configuration.md", r"`GET …/release/versions/<id>`"),
        ("docs/configuration.md", r"`PUT …/release/versions/<id>/listing`"),
        ("docs/configuration.md", r"`GET …/release/versions/<id>/diff`"),
        ("docs/configuration.md", r"`DELETE …/release/versions/<id>`"),
        ("docs/configuration.md", r"^\*\*`version_id`\.\*\*"),
        ("docs/configuration.md", r"\| `version_exists` \|"),
        ("docs/configuration.md", r"\| `version_closed` \|"),
        ("docs/configuration.md", r"\| `version_running` \|"),
        ("docs/configuration.md", r"\| `listing_json_unsupported` \|"),
        ("docs/configuration.md", r"\| `split_version_unsupported` \|"),
        ("docs/configuration.md", r"404\n`version_not_found`"),
        ("docs/configuration.md", r"the `versions` table behind the version routes, alone"),
        ("CHANGELOG.md", r"Database schema v21"),
        ("CHANGELOG.md", r"`GET /api/repos/<repo>/release/versions`"),
        ("CHANGELOG.md", r"`version_id` on plan, review, upload and start"),
    ],
)
def test_the_routes_are_documented(path, pattern):
    text = (Path(__file__).resolve().parents[1] / path).read_text()
    assert re.search(pattern, text, re.M), f"{path} lacks /{pattern}/"
