"""`rcm release golive` · `rcm release status` — 승인된 빌드의 출시를 CLI 에서 묻고 건다(M5s §4.5 ·
결정 235).

여기서 지키는 것:
- `--plan` 은 스토어를 읽기만 하는 잡을 내고 끝날 때까지 기다린 뒤 승인 여부와 승인된 빌드를
  그린다. 번호 · 시각을 같이 주면 사용 오류다.
- `--plan` 없이는 사람이 친 `--confirm-build-number` 가 있어야 한다. `--at`·`--in` 은 서버에
  시각으로 그대로 간다. plan 이 없거나 낡았다는 409 에는 `--plan` 으로 가는 한 줄을 덧붙인다.
- 저장소는 자리 인자로도 `--repo` 로도 받는다. 둘이 다르면 사용 오류다.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

import remote_ci_monitor.cli as cli
from remote_ci_monitor.config import RepoConfig, parse_preset, parse_release_profile
from remote_ci_monitor.core.model import SCHEDULED
from test_cli_m4 import run
from test_cli_release import cli_home, use  # noqa: F401 — 픽스처
from test_release_golive import BOTH_APPROVED, GOLIVE, GOLIVE_PLAN, GoliveServer
from test_release_state import STATUS
from test_release_status_panel import STATUS_PRESET
from test_server_release_routes import NOW, PROFILE
from test_server_release_routes import remote as _remote
from test_server_release_versions import VersionServer

STATUS_NAME = "release-status"

remote = _remote


class CliGoliveServer(GoliveServer):
    """golive 에 더해 E(#254)의 `status` 역할도 있는 `app` — `rcm release status` 를 진짜
    라우트로 돌린다."""

    finish_job = VersionServer.finish_job

    def __init__(self, tmp_path, remote, **overrides):
        super().__init__(tmp_path, remote, **overrides)
        raw = {
            **PROFILE,
            "presets": {**PROFILE["presets"], "golive": GOLIVE, "status": STATUS_NAME},
        }
        self.cfg.repos = (
            RepoConfig(name="app", url=remote.url, release=parse_release_profile("app", raw)),
        )
        self.cfg.presets = (parse_preset(STATUS_PRESET), *self.cfg.presets)


@pytest.fixture
def csrv(tmp_path: Path, remote, monkeypatch, cli_home):  # noqa: F811
    s = CliGoliveServer(tmp_path / "srv", remote)
    try:
        s.open_gate(monkeypatch)
        use(monkeypatch, s)
        yield s
    finally:
        s.close()


def finish_on_wait(monkeypatch, srv: CliGoliveServer, files: dict[str, bytes], *, code=0):
    """`wait_for_job` 대역 — 기다리는 잡을 워커처럼 끝내고 그 잡 문서로 돌아온다."""
    seen: list[int] = []

    def fake(client, job_id, **_kw):
        seen.append(job_id)
        srv.finish_job(job_id, files, state="succeeded" if code == 0 else "failed", exit_code=code)
        return code, client.job(job_id), None

    monkeypatch.setattr(cli, "wait_for_job", fake)
    return seen


def plan_files(doc: dict) -> dict[str, bytes]:
    return {"build/.rcm-release/golive-plan.json": json.dumps(doc).encode()}


# ── --plan ───────────────────────────────────────────────────────────────────


def test_plan_waits_for_the_read_and_prints_the_approval_and_the_next_command(
    csrv, monkeypatch, capsys
):
    doc = {**GOLIVE_PLAN, "measured_at": NOW.isoformat().replace("+00:00", "Z")}
    seen = finish_on_wait(monkeypatch, csrv, plan_files(doc))
    argv = ["release", "golive", "app", "--build-name", "1.0.1", "--platform", "both", "--plan"]
    code, out, err = run(capsys, argv)
    assert code == 0, out + err
    job = csrv.store.get_job(seen[0])
    assert job.preset == GOLIVE
    assert job.inputs["mode"] == "plan" and job.inputs["platform"] == "both"
    lines = out.strip().splitlines()
    assert lines[0] == f"golive plan #{seen[0]} succeeded · build 1.0.1 · verdict blocked"
    assert lines[1].split() == ["iOS", "approved", "build", "185", "PENDING_DEVELOPER_RELEASE"]
    assert lines[2].split()[:4] == ["Android", "not", "approved", "build"]
    assert lines[3] == (
        "next: rcm release golive app --build-name 1.0.1 --platform ios "
        "--confirm-build-number 185 [--at HH:MM | --in 30m]"
    )
    assert "no release" in err


def test_plan_json_carries_the_plan_entry(csrv, monkeypatch, capsys):
    finish_on_wait(monkeypatch, csrv, plan_files(BOTH_APPROVED))
    code, out, err = run(
        capsys,
        ["release", "golive", "--repo", "app", "--build-name", "1.0.1", "--plan", "--json"],
    )
    assert code == 0, out + err
    doc = json.loads(out)
    assert doc["state"] == "succeeded" and doc["plan"]["doc"]["plan_verdict"] == "ok"


def test_plan_takes_no_number_and_no_time(csrv, capsys):
    for extra in (["--confirm-build-number", "185"], ["--at", "15:00"], ["--in", "5m"]):
        code, out, err = run(
            capsys, ["release", "golive", "app", "--build-name", "1.0.1", "--plan", *extra]
        )
        assert code == 2 and "--plan only reads the stores" in err, err
    assert csrv.store.list_jobs_by_preset([GOLIVE], 10) == []


# ── 출시 요청 ─────────────────────────────────────────────────────────────────


def test_release_without_a_typed_number_is_a_usage_error(csrv, capsys):
    code, out, err = run(capsys, ["release", "golive", "app", "--build-name", "1.0.1"])
    assert code == 2 and "--confirm-build-number N is required" in err
    assert csrv.store.list_jobs_by_preset([GOLIVE], 10) == []


def test_release_with_in_books_a_scheduled_job_and_says_how_to_follow_it(csrv, capsys):
    csrv.golive_plan_job()
    code, out, err = run(
        capsys,
        [
            "release",
            "golive",
            "app",
            "--build-name",
            "1.0.1",
            "--platform",
            "ios",
            "--confirm-build-number",
            "185",
            "--in",
            "2h",
        ],
    )
    assert code == 0, out + err
    jobs = [j for j in csrv.store.list_jobs_by_preset([GOLIVE], 10) if j.state == SCHEDULED]
    assert len(jobs) == 1
    job = jobs[0]
    assert job.inputs["mode"] == "release" and job.inputs["confirm_build_number"] == "185"
    assert job.not_before == NOW + timedelta(hours=2)
    lines = out.strip().splitlines()
    assert lines[0].startswith(f"golive #{job.id} scheduled for ")
    assert lines[0].endswith("· build 1.0.1 · ios 185")
    assert lines[1] == f"follow: rcm wait --job {job.id} · cancel: rcm cancel {job.id}"


def test_a_missing_plan_points_at_plan_first(csrv, capsys):
    code, out, err = run(
        capsys,
        ["release", "golive", "app", "--build-name", "1.0.1", "--confirm-build-number", "185"],
    )
    assert code == 2, out + err
    assert "no golive plan for build 1.0.1" in err
    assert "hint: rcm release golive app --build-name 1.0.1 --platform both --plan" in err


def test_a_client_token_cannot_go_live(csrv, monkeypatch, capsys):
    csrv.golive_plan_job()
    use(monkeypatch, csrv, "alice")
    code, out, err = run(
        capsys,
        [
            "release",
            "golive",
            "app",
            "--build-name",
            "1.0.1",
            "--platform",
            "ios",
            "--confirm-build-number",
            "185",
        ],
    )
    assert code == 2 and "admin token" in err, err


def test_two_different_repositories_are_a_usage_error(csrv, capsys):
    code, out, err = run(
        capsys, ["release", "golive", "app", "--repo", "other", "--build-name", "1.0.1", "--plan"]
    )
    assert code == 2 and "two repositories given" in err


def test_post_jobs_refusal_reaches_rcm_run_with_the_route_name(csrv, capsys):
    """S23 — `rcm run <golive 프리셋> -f mode=release` 는 서버가 409 로 거절하고 길을 말한다."""
    code, out, err = run(
        capsys,
        [
            "run",
            GOLIVE,
            "--ref",
            "main",
            "-f",
            "build_name=1.0.1",
            "-f",
            "mode=release",
            "-f",
            "confirm_build_number=185",
            "--no-wait",
        ],
    )
    assert code == 2, out + err
    assert "release/golive" in err and "rcm release golive app" in err


# ── rcm release status (E 의 `POST …/release/status` 위에서) ──────────────────


def test_status_runs_the_status_role_waits_and_prints_both_stores(csrv, monkeypatch, capsys):
    """E(#254)의 라우트로 status 잡을 내고, 끝나기를 기다린 뒤 두 스토어를 한 줄씩 그린다.
    목록 밖의 state 는 그대로가 아니라 unknown 으로 그린다(§4.4)."""
    doc = {
        **STATUS,
        "checked_at": NOW.isoformat().replace("+00:00", "Z"),
        "android": {**STATUS["android"], "state": "SOMETHING_NEW"},
    }
    seen = finish_on_wait(
        monkeypatch, csrv, {"build/.rcm-release/status.json": json.dumps(doc).encode()}
    )
    code, out, err = run(capsys, ["release", "status", "app", "--build-name", "1.2.0"])
    assert code == 0, out + err
    job = csrv.store.get_job(seen[0])
    assert job.preset == STATUS_NAME and job.inputs.get("build_name") == "1.2.0"
    lines = out.strip().splitlines()
    assert lines[0].startswith(f"status #{seen[0]} succeeded · checked 2026-09-17T12:00:00Z")
    assert lines[1].split() == [
        "iOS",
        "1.2.0",
        "build",
        "185",
        "approved",
        "PENDING_DEVELOPER_RELEASE",
    ]
    assert lines[2].split() == [
        "Android",
        "1.2.0",
        "build",
        "185",
        "unknown",
        "RELEASE_LIFECYCLE_STATE_IN_REVIEW",
    ]
    assert "reading the stores" in err


def test_status_json_and_an_empty_build_name(csrv, monkeypatch, capsys):
    seen = finish_on_wait(monkeypatch, csrv, {"status.json": json.dumps(STATUS).encode()})
    code, out, err = run(capsys, ["release", "status", "--repo", "app", "--json"])
    assert code == 0, out + err
    assert csrv.store.get_job(seen[0]).inputs.get("build_name", "") == ""
    doc = json.loads(out)
    assert doc["job_id"] == seen[0] and doc["status"]["doc"]["ios"]["state"] == "approved"


def test_status_with_a_client_token_works_and_a_failed_read_says_so(csrv, monkeypatch, capsys):
    """읽기만 하는 역할이라 클라이언트 토큰이면 된다. 스크립트가 아무것도 못 쓰고 실패하면
    없는 문서를 지어내지 않고 그 사실과 잡의 종료 코드를 돌려준다."""
    use(monkeypatch, csrv, "alice")
    finish_on_wait(monkeypatch, csrv, None, code=1)
    code, out, err = run(capsys, ["release", "status", "app"])
    assert code == 1, out + err
    assert "left no status.json" in err
