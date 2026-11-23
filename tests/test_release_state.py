"""릴리스 상태(`release_state.py`) — 역할별 최신 · 문서 읽기 · 나이와 stale · 번호 비교.

명세: 스토어 탭 API 계약 v2 `GET …/release` · docs/release-contract.md §2 · §6.
순수 규칙만: 잡은 팩토리로, 묶음 파일은 dict 로, 문서 읽기는 함수로 넘긴다. 네트워크도 DB 도 없다.
"""

from __future__ import annotations

import dataclasses
import io
import json
import tarfile
from datetime import timedelta
from pathlib import Path

import pytest

from jobfactory import NOW, ago, job
from remote_ci_monitor.core.model import FAILED, RUNNING, SUCCEEDED, Source
from remote_ci_monitor.release_state import (
    JOBS_LIMIT,
    LISTED_ROLES,
    MAX_DOC_BYTES,
    ROLE_FILES,
    artifact_names,
    numbers_match,
    parse_iso,
    plan_number,
    read_bundle_member,
    release_view,
    role_entry,
)

PRESETS = {
    "plan": "release-plan",
    "upload": "release-upload",
    "review": "release-review",
    "gate": None,
    "qa": None,
    "dev": None,
    "version": None,
    "status": "release-status",
    "golive": "release-golive",
}


def git_job(id: int, preset: str, state: str = SUCCEEDED, *, finished_min: float = 5):
    j = job(
        id,
        key=preset,
        state=state,
        preset=preset,
        inputs={"build_name": "1.0.1"},
        started_min=finished_min + 3,
        finished_min=None if state == RUNNING else finished_min,
    )
    src = Source(mode="git_ref", repo="app", ref="main", sha="a" * 40, base_sha="a" * 40)
    return dataclasses.replace(j, source=src)


def files(*paths: str) -> list[dict]:
    return [{"path": p, "size": 3, "sha256": "0" * 64, "mode": 0o644} for p in paths]


def reader(docs: dict[tuple[int, str], bytes]):
    def read(job_id: int, member: str) -> bytes:
        return docs[(job_id, member)]

    return read


PLAN = {"schema": 1, "build_name": "1.0.1", "n": 181, "measured_at": "2026-09-04T00:48:12Z"}


def view(jobs, bundles, docs, *, max_age=30, now=NOW):
    return release_view(
        jobs,
        PRESETS,
        lambda jid: bundles.get(jid, []),
        reader(docs),
        now=now,
        max_age_minutes=max_age,
    )


# ── 역할별 최신 ──────────────────────────────────────────────────────────────


def test_latest_per_role_is_the_newest_job_whose_bundle_has_the_role_file():
    jobs = [
        git_job(3, "release-plan", RUNNING),  # 돌고 있다 — 산출물이 없으니 후보가 아니다
        git_job(2, "release-plan"),
        git_job(1, "release-plan", finished_min=60),
    ]
    bundles = {2: files("out/plan.json"), 1: files("plan.json")}
    docs = {(2, "out/plan.json"): json.dumps(PLAN).encode(), (1, "plan.json"): b"{}"}
    v = view(jobs, bundles, docs)
    assert v["plan"]["job_id"] == 2  # basename 으로 맞춘다 — 묶음 안 어디에 있든
    assert v["plan"]["doc"] == PLAN and v["plan"]["build_name"] == "1.0.1"
    assert v["plan"]["stale"] is False and "doc_error" not in v["plan"]
    assert v["plan"]["measured_at"] == "2026-09-04T00:48:12Z"
    assert v["plan"]["age_seconds"] == 240
    assert v["review"] == {"plan": None, "result": None} and v["upload"] is None
    assert [r["id"] for r in v["jobs"]] == [3, 2, 1]
    assert v["jobs"][0] == {
        "id": 3,
        "preset": "release-plan",
        "role": "plan",
        "state": "running",
        "sha": "a" * 40,
        "ref": "main",
        "started_at": ago(minutes=8).isoformat().replace("+00:00", "Z"),
        "finished_at": None,
        "artifacts": [],
    }
    assert v["jobs"][1]["artifacts"] == ["plan.json"]


def test_a_newer_job_without_the_file_does_not_hide_the_older_one_with_it():
    jobs = [git_job(2, "release-plan", FAILED), git_job(1, "release-plan", finished_min=20)]
    bundles = {2: files("log.txt"), 1: files("plan.json")}
    v = view(jobs, bundles, {(1, "plan.json"): json.dumps(PLAN).encode()})
    assert v["plan"]["job_id"] == 1 and v["plan"]["state"] == "succeeded"


def test_a_failed_job_with_a_plan_file_is_still_the_latest_plan_and_says_its_state():
    """계획은 「어떻게 끝나든 쓴다」(계약 §2) — blocked(exit 1)도 plan.json 이 있다. 상태는 그대로
    싣고, 되돌릴 수 없는 문을 여는 건 서버의 규칙(succeeded 만)이다."""
    jobs = [git_job(1, "release-plan", FAILED)]
    doc = {**PLAN, "n": None, "blockers": [{"code": "B-PLAYBUSY", "text": "…"}]}
    v = view(jobs, {1: files("plan.json")}, {(1, "plan.json"): json.dumps(doc).encode()})
    assert v["plan"]["state"] == "failed" and v["plan"]["doc"]["n"] is None
    assert plan_number(v["plan"]) is None


def test_review_has_two_files_for_two_modes():
    jobs = [git_job(2, "release-review"), git_job(1, "release-review", finished_min=9)]
    bundles = {2: files("review.json"), 1: files("review-plan.json")}
    docs = {
        (2, "review.json"): b'{"overall_status": "submitted"}',
        (1, "review-plan.json"): b'{"plan_verdict": "ok", "build_name": "1.0.1", "n": 181}',
    }
    v = view(jobs, bundles, docs)
    assert v["review"]["result"] == {
        "job_id": 2,
        "state": "succeeded",
        "doc": {"overall_status": "submitted"},
    }
    assert v["review"]["plan"]["job_id"] == 1 and v["review"]["plan"]["build_name"] == "1.0.1"
    assert v["review"]["plan"]["measured_at"] == ago(minutes=9).isoformat().replace("+00:00", "Z")
    assert v["review"]["plan"]["age_seconds"] == 540 and v["review"]["plan"]["stale"] is False


def test_jobs_are_newest_first_and_capped_at_50_roles_only():
    jobs = [git_job(i, "release-plan") for i in range(80, 0, -1)]
    jobs.insert(0, git_job(99, "gate", preset := "other"))  # 역할 프리셋이 아니다
    del preset
    v = view(jobs, {}, {})
    assert len(v["jobs"]) == JOBS_LIMIT == 50
    assert v["jobs"][0]["id"] == 80 and 99 not in {r["id"] for r in v["jobs"]}


# ── status · golive 역할 (M5s §4.4 · §4.5) ─────────────────────────────────────

STATUS = {
    "schema": 1,
    "checked_at": "2026-09-04T00:50:12Z",
    "ios": {
        "version": "1.2.0",
        "build": 185,
        "state": "approved",
        "raw_state": "PENDING_DEVELOPER_RELEASE",
        "release_type": "MANUAL",
        "phased": None,
    },
    "android": {
        "version": "1.2.0",
        "version_code": 185,
        "state": "in_review",
        "raw_state": "RELEASE_LIFECYCLE_STATE_IN_REVIEW",
        "user_fraction": None,
    },
}
GOLIVE_PLAN = {
    "schema": 1,
    "build_name": "1.2.0",
    "measured_at": "2026-09-04T00:44:12Z",
    "platforms": {
        "ios": {"approved": True, "approved_build": 185, "raw_state": "PENDING_DEVELOPER_RELEASE"},
        "android": {"approved": False, "approved_build": None, "raw_state": "IN_REVIEW"},
    },
    "plan_verdict": "ok",
    "reasons": [],
}
GOLIVE = {
    "schema": 1,
    "build_name": "1.2.0",
    "platforms": {"ios": {"result": "requested", "raw_state": "PENDING_DEVELOPER_RELEASE"}},
    "finished_at": "2026-09-04T00:51:12Z",
}


def test_status_and_golive_are_listed_roles_with_their_files():
    """두 역할은 선택 역할로 등록된다 — 산출물 파일은 status 하나, golive 둘(plan · release)."""
    assert ROLE_FILES["status"] == ("status.json",)
    assert ROLE_FILES["golive"] == ("golive-plan.json", "golive.json")
    assert "status" in LISTED_ROLES and "golive" in LISTED_ROLES


def test_an_empty_view_has_status_null_and_golive_plan_and_result_null():
    v = view([], {}, {})
    assert v["status"] is None
    assert v["golive"] == {"plan": None, "result": None}


def test_status_is_the_latest_status_json_aged_from_checked_at():
    """status.json 은 `measured_at` 이 아니라 `checked_at` 으로 잰다(§4.4 의 최소 필드)."""
    jobs = [git_job(2, "release-status", RUNNING), git_job(1, "release-status")]
    v = view(
        jobs, {1: files("out/status.json")}, {(1, "out/status.json"): json.dumps(STATUS).encode()}
    )
    assert v["status"] == {
        "job_id": 1,
        "state": "succeeded",
        "measured_at": "2026-09-04T00:50:12Z",
        "age_seconds": 120,
        "stale": False,
        "doc": STATUS,
    }
    assert [(r["id"], r["role"]) for r in v["jobs"]] == [(2, "status"), (1, "status")]


def test_a_status_without_checked_at_is_aged_from_the_job_and_goes_stale():
    doc = {k: v for k, v in STATUS.items() if k != "checked_at"}
    jobs = [git_job(1, "release-status", finished_min=45)]
    v = view(jobs, {1: files("status.json")}, {(1, "status.json"): json.dumps(doc).encode()})
    assert v["status"]["measured_at"] == ago(minutes=45).isoformat().replace("+00:00", "Z")
    assert v["status"]["age_seconds"] == 45 * 60 and v["status"]["stale"] is True


def test_golive_has_a_plan_and_a_result_from_two_files():
    jobs = [git_job(2, "release-golive"), git_job(1, "release-golive", finished_min=8)]
    bundles = {2: files("golive.json"), 1: files("golive-plan.json")}
    docs = {
        (2, "golive.json"): json.dumps(GOLIVE).encode(),
        (1, "golive-plan.json"): json.dumps(GOLIVE_PLAN).encode(),
    }
    v = view(jobs, bundles, docs)
    assert v["golive"]["result"] == {"job_id": 2, "state": "succeeded", "doc": GOLIVE}
    plan = v["golive"]["plan"]
    assert plan["job_id"] == 1 and plan["build_name"] == "1.2.0" and plan["doc"] == GOLIVE_PLAN
    assert plan["measured_at"] == "2026-09-04T00:44:12Z"
    assert plan["age_seconds"] == 480 and plan["stale"] is False
    # 다른 역할의 칸은 건드리지 않는다
    assert v["plan"] is None and v["review"] == {"plan": None, "result": None}
    assert v["status"] is None


# ── 문서 읽기 ────────────────────────────────────────────────────────────────


def test_a_broken_document_is_null_with_a_reason_never_a_guess():
    jobs = [git_job(1, "release-plan")]
    v = view(jobs, {1: files("plan.json")}, {(1, "plan.json"): b"{not json"})
    assert v["plan"]["doc"] is None and v["plan"]["doc_error"].startswith("not valid JSON")
    assert v["plan"]["build_name"] is None
    v = view(jobs, {1: files("plan.json")}, {(1, "plan.json"): b"[1]"})
    assert v["plan"]["doc"] is None and v["plan"]["doc_error"] == "not a JSON object"


def test_an_unreadable_bundle_is_reported_not_raised():
    def boom(_jid: int, _member: str) -> bytes:
        raise FileNotFoundError("gone")

    entry = role_entry(git_job(1, "release-plan"), "plan.json", boom)
    assert entry == {
        "job_id": 1,
        "state": "succeeded",
        "doc": None,
        "doc_error": "cannot read plan.json: FileNotFoundError",
    }


def test_measured_at_falls_back_to_finished_at_when_the_document_has_none():
    jobs = [git_job(1, "release-plan", finished_min=31)]
    v = view(jobs, {1: files("plan.json")}, {(1, "plan.json"): b'{"n": 5}'})
    assert v["plan"]["measured_at"] == ago(minutes=31).isoformat().replace("+00:00", "Z")
    assert v["plan"]["age_seconds"] == 31 * 60 and v["plan"]["stale"] is True


@pytest.mark.parametrize(
    "age_min,max_age,stale", [(29, 30, False), (30, 30, False), (31, 30, True), (2, 1, True)]
)
def test_stale_is_age_over_plan_max_age_minutes(age_min, max_age, stale):
    measured = (NOW - timedelta(minutes=age_min)).isoformat().replace("+00:00", "Z")
    doc = json.dumps({**PLAN, "measured_at": measured}).encode()
    v = view(
        [git_job(1, "release-plan")],
        {1: files("plan.json")},
        {(1, "plan.json"): doc},
        max_age=max_age,
    )
    assert v["plan"]["stale"] is stale and v["plan"]["age_seconds"] == age_min * 60


def test_a_bad_measured_at_is_not_a_date_and_the_job_time_is_used():
    doc = json.dumps({**PLAN, "measured_at": "yesterday"}).encode()
    v = view([git_job(1, "release-plan")], {1: files("plan.json")}, {(1, "plan.json"): doc})
    assert v["plan"]["measured_at"] == ago(minutes=5).isoformat().replace("+00:00", "Z")
    assert parse_iso("yesterday") is None and parse_iso(None) is None
    assert parse_iso("2026-09-04T00:00:00+09:00").utcoffset() == timedelta(hours=9)


def test_read_bundle_member_reads_the_file_from_the_tar_and_refuses_big_ones(tmp_path: Path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data in {
            "out/plan.json": b'{"n": 1}',
            "big.json": b"x" * (MAX_DOC_BYTES + 1),
        }.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    path = tmp_path / "bundle.tar"
    path.write_bytes(buf.getvalue())
    assert read_bundle_member(path, "out/plan.json") == b'{"n": 1}'
    with pytest.raises(ValueError):
        read_bundle_member(path, "big.json")
    with pytest.raises(KeyError):
        read_bundle_member(path, "missing.json")


# ── 이름 · 번호 ──────────────────────────────────────────────────────────────


def test_artifact_names_are_basenames_in_order_without_duplicates():
    assert artifact_names(files("a/plan.json", "b/plan.json", "log.txt")) == [
        "plan.json",
        "log.txt",
    ]
    assert artifact_names([{"size": 1}, "x"]) == []


@pytest.mark.parametrize(
    "n,typed,ok",
    [
        (181, "181", True),
        (181, 181, True),
        (181, " 181 ", True),
        (181, "0181", True),
        (181, "182", False),
        (181, "", False),
        (181, None, False),
        (None, "181", False),
        (181, True, False),
    ],
)
def test_numbers_match_compares_as_integers_and_never_fills_in(n, typed, ok):
    assert numbers_match(typed, n) is ok


def test_plan_number_takes_ints_and_digit_strings_only():
    assert plan_number({"doc": {"n": 181}}) == 181
    assert plan_number({"doc": {"n": "181"}}) == 181
    assert plan_number({"doc": {"n": True}}) is None
    assert plan_number({"doc": {"n": "abc"}}) is None
    assert plan_number({"doc": None}) is None and plan_number(None) is None
