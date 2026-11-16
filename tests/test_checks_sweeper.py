"""M5n 자동 점검 스위퍼와 다시 점검(역할 B) — (워커, 점검, 지문)마다 한 번 · down 워커 제외 ·
ref 해석 실패 · `POST /machines/{id}/recheck`.

명세는 docs/m5n-workplan.md §4.4 · §4.5 · §8.1 · §14.1 edge 1. 시나리오 표는
docs/m5n-test-scenarios-b.md. 구현 전이라 빨간 것이 정상이다.

- 서버는 `tests/test_server_m5n.py` 의 `M5nServer`(in-process HTTP · 주입 시계 · 로컬 레인
  안 띄움). 스위퍼 스레드도 안 띄운다 — 한 회차를 `App.sweep_checks()` 로 직접 부른다
  (가정 B-10: 이름과 반환값 = 이번 회차에 건 잡 id 목록). 잠도 벽시계도 없다.
- 점검 프리셋 `simcheck` 은 `git_ref` 다. 스위퍼가 `check_ref`(기본 "HEAD")를 `ls-remote` 로
  푸는 원격은 `tests/gitrepo.build_remote` 의 tmp bare 레포다(네트워크 없음).
- `scenario`(풀 mac2)는 `checks = ["simcheck"]` · `requires = [FAKE_TOOL]` — 도구가 없는 로컬은
  `tool_missing` 이 먼저라 점검이 필요 없다. 그래서 점검이 걸리는 워커는 보고한 `build-02` 하나다.
"""

from __future__ import annotations

import shutil
from datetime import timedelta
from typing import Any

import pytest

from gitrepo import build_remote
from remote_ci_monitor.core.model import QUEUED
from test_server_m5n import (
    PRESETS,
    M5nServer,
    expected_hash,
    ok_probe,
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")


@pytest.fixture
def remote(tmp_path):
    root = tmp_path / "remote"
    root.mkdir()
    return build_remote(root)


@pytest.fixture
def srv(tmp_path, remote):
    s = M5nServer(tmp_path, repo_url=remote.url)
    yield s
    s.close()


def sweep(srv: M5nServer) -> list[int]:
    return list(srv.app.sweep_checks())


def check_jobs(srv: M5nServer) -> list[int]:
    rows = srv.store._conn().execute("SELECT id FROM jobs WHERE preset='simcheck' ORDER BY id")
    return [int(r["id"]) for r in rows]


def checks_of(srv: M5nServer, worker: str = "build-02") -> dict[str, dict[str, Any]]:
    return {c["preset"]: c for c in srv.machines()[worker]["checks"]}


def verdict_of(srv: M5nServer, preset: str, worker: str = "build-02") -> tuple[str, str]:
    v = srv.machines()[worker]["presets"][preset]
    return v["state"], v["code"]


def run_check(srv: M5nServer, jid: int, outcome: str) -> None:
    """build-02 가 그 점검 잡을 집어 `outcome` 으로 끝낸다(HTTP 워커 경로 그대로)."""
    assert srv.claimed("build-02") == jid
    exit_code = 0 if outcome == "succeeded" else 1
    assert srv.finish("build-02", jid, outcome, exit_code=exit_code)[0] == 200


# ── 1. 한 번만 (결정 95 · edge 1) ─────────────────────────────────────────────


def test_a_needed_check_is_submitted_once_targeted_at_that_worker(srv, remote):
    srv.reported("build-02")
    assert verdict_of(srv, "scenario") == ("unknown", "check_needed")
    ids = sweep(srv)
    assert len(ids) == 1
    job = srv.store.get_job(ids[0])
    assert job.preset == "simcheck"
    assert job.state == QUEUED
    assert job.target_worker == "build-02"
    assert job.pool == "mac2"
    assert (job.requester.name, job.requester.label) == ("rcm:check", "rcm (machine check)")
    assert job.priority == 1  # high
    assert job.sample is False
    assert (job.source.mode, job.source.sha) == ("git_ref", remote.main)  # check_ref "HEAD"
    # 가정 B-7: 대기 중에도 「이 지문의 점검이 돌고 있다」로 읽히게 제출 때 지문을 박는다
    fp = expected_hash({"xc": "Xc 1"})
    assert job.fingerprint_hash == fp
    assert srv.store.auto_check_done("build-02", "simcheck", fp) is True
    assert verdict_of(srv, "scenario") == ("unknown", "check_running")
    assert checks_of(srv)["simcheck"] == {
        "preset": "simcheck",
        "state": "running",
        "job_id": ids[0],
        "auto_submitted": True,
        "error_code": None,
        "recheck_needed": False,
    }


def test_a_second_sweep_does_not_submit_again(srv):
    srv.reported("build-02")
    first = sweep(srv)
    assert sweep(srv) == []
    assert check_jobs(srv) == first


def test_a_passed_check_opens_the_preset(srv):
    srv.reported("build-02")
    [jid] = sweep(srv)
    run_check(srv, jid, "succeeded")
    assert verdict_of(srv, "scenario") == ("eligible", "ok")
    assert checks_of(srv)["simcheck"]["state"] == "passed"
    assert sweep(srv) == []


def test_a_failed_check_is_not_submitted_again(srv):
    srv.reported("build-02")
    [jid] = sweep(srv)
    run_check(srv, jid, "failed")
    assert verdict_of(srv, "scenario") == ("ineligible", "check_failed")
    assert checks_of(srv)["simcheck"]["state"] == "failed"
    assert sweep(srv) == []
    assert sweep(srv) == []
    assert check_jobs(srv) == [jid]


def test_a_cancelled_check_is_not_submitted_again_automatically(srv):
    srv.reported("build-02")
    [jid] = sweep(srv)
    status, body = srv.cancel(jid, token="admin")
    assert status == 200, body
    assert srv.store.get_job(jid).state == "cancelled"
    # 취소된 점검은 「점검 필요」로 돌아가지만, 자동으로는 다시 안 건다 — 사람이 건다
    assert checks_of(srv)["simcheck"]["state"] == "needed"
    assert sweep(srv) == []


def test_a_lost_check_is_not_submitted_again_automatically(srv):
    srv.reported("build-02")
    [jid] = sweep(srv)
    assert srv.claimed("build-02") == jid
    srv.clock.advance(61)
    assert jid in srv.app.mark_lost_workers(srv.clock.now)
    assert srv.heartbeat("build-02")[0] == 200  # 다시 살아났다
    assert sweep(srv) == []


def test_a_lost_automatic_check_makes_the_waiting_job_ask_for_a_person(srv):
    """실측(2026-11-16): 자동 점검 도중 워커가 재시작돼 유실 → 결정 95 로 다시 안 건다. 그런데
    대기 잡의 이유가 `check_pending`(「점검 중 · 스스로 풀림」, 확인 목록에 안 오름)이라 잡이
    조용히 영원히 기다렸다. 스스로 안 풀리는 점검은 사람이 볼 이유(`check_failed`)여야 한다."""
    srv.reported("build-02")
    qa = srv.queued("scenario")
    assert srv.row(qa)["reason"] == "check_pending"  # 아직 안 걸었다 — 스위퍼가 곧 건다
    [jid] = sweep(srv)
    assert srv.row(qa)["reason"] == "check_pending"  # 기다리는 중 — 스스로 풀린다
    assert srv.claimed("build-02") == jid
    srv.clock.advance(61)
    assert jid in srv.app.mark_lost_workers(srv.clock.now)
    assert srv.heartbeat("build-02")[0] == 200
    assert sweep(srv) == []
    row = srv.row(qa)
    assert row["reason"] == "check_failed", row["reason"]
    [note] = [n for n in row["placement_detail"] if n["worker"] == "build-02"]
    assert note["code"] == "check_needed" and note["detail"]["recheck"] is True, note
    assert checks_of(srv)["simcheck"]["recheck_needed"] is True
    assert srv.machines()["build-02"]["presets"]["scenario"]["detail"]["recheck"] is True


def test_with_auto_checks_off_a_needed_check_asks_for_a_person(tmp_path, remote):
    s = M5nServer(tmp_path, repo_url=remote.url, extra="auto_checks = false\n")
    try:
        s.reported("build-02")
        qa = s.queued("scenario")
        assert s.row(qa)["reason"] == "check_failed"
        assert {c["preset"]: c for c in s.machines()["build-02"]["checks"]}["simcheck"][
            "recheck_needed"
        ] is True
    finally:
        s.close()


def test_a_changed_fingerprint_gets_exactly_one_new_check(srv):
    """edge 1 — 지문이 또 바뀌면 다시 걸린다."""
    gen = srv.reported("build-02")
    [first] = sweep(srv)
    run_check(srv, first, "failed")
    assert srv.report("build-02", gen, probes={"xc": ok_probe("Xc 2")})[0] == 200
    assert verdict_of(srv, "scenario") == ("unknown", "check_needed")
    second = sweep(srv)
    assert len(second) == 1
    assert srv.store.get_job(second[0]).fingerprint_hash == expected_hash({"xc": "Xc 2"})
    assert sweep(srv) == []
    assert check_jobs(srv) == [first, *second]


def test_the_xcode_incident_replayed_end_to_end_in_process(srv):
    """edge 1 — 통과 → 지문 바뀜 → 점검 필요 → 자동 점검 한 번 → 실패 → 다시 안 건다 → 또 바뀌면
    한 번 더."""
    gen = srv.reported("build-02")
    [ok_run] = sweep(srv)
    run_check(srv, ok_run, "succeeded")
    assert verdict_of(srv, "scenario") == ("eligible", "ok")
    srv.clock.advance(60)
    assert srv.report("build-02", gen, probes={"xc": ok_probe("Xc 2")})[0] == 200
    assert verdict_of(srv, "scenario") == ("unknown", "check_needed")
    [bad_run] = sweep(srv)
    run_check(srv, bad_run, "failed")
    assert verdict_of(srv, "scenario") == ("ineligible", "check_failed")
    for _ in range(3):
        assert sweep(srv) == []
    m = srv.machines()["build-02"]
    assert m["fingerprint"]["probes"]["xc"]["previous"] == "Xc 1"
    assert m["checks"][0]["job_id"] == bad_run
    assert srv.report("build-02", gen, probes={"xc": ok_probe("Xc 3")})[0] == 200
    assert len(sweep(srv)) == 1


# ── 2. 걸지 않는 경우 ─────────────────────────────────────────────────────────


def test_a_down_worker_gets_no_check_until_it_is_back(srv):
    srv.reported("build-02")
    srv.clock.advance(61)
    assert sweep(srv) == []
    assert srv.heartbeat("build-02")[0] == 200
    assert len(sweep(srv)) == 1


def test_auto_checks_off_submits_nothing(tmp_path, remote):
    s = M5nServer(tmp_path, repo_url=remote.url, extra="auto_checks = false\n")
    try:
        s.reported("build-02")
        assert sweep(s) == []
        assert verdict_of(s, "scenario") == ("unknown", "check_needed")
    finally:
        s.close()


def test_an_unresolvable_ref_skips_the_round_and_says_ref_unresolved(tmp_path):
    """원격이 없다 — 그 회차는 건너뛰고, 1회 제한도 쓰지 않는다(다음 회차가 다시 해 본다)."""
    s = M5nServer(tmp_path)  # repo url = tmp 안의 없는 경로
    try:
        s.reported("build-02")
        assert sweep(s) == []
        assert checks_of(s)["simcheck"] == {
            "preset": "simcheck",
            "state": "needed",
            "job_id": None,
            "auto_submitted": False,
            "error_code": "ref_unresolved",
            "recheck_needed": False,  # 다음 회차가 다시 해 본다
        }
        fp = expected_hash({"xc": "Xc 1"})
        assert s.store.auto_check_done("build-02", "simcheck", fp) is False
        assert check_jobs(s) == []
    finally:
        s.close()


def test_an_unresolvable_ref_is_logged_once_not_every_sweep(tmp_path, capsys):
    """실측(2026-11-16, check_sweep_seconds = 5 · 워커 4): 같은 줄이 회차마다 워커마다 찍혀 15초에
    20줄이었다(시간당 수천 줄). 처음 한 번만 — 상태는 `rcm machines` 의 `ref_unresolved` 가 말한다."""
    s = M5nServer(tmp_path)  # repo url = tmp 안의 없는 경로
    try:
        s.reported("build-02")
        capsys.readouterr()
        for _ in range(4):
            assert sweep(s) == []
        err = capsys.readouterr().err
        assert err.count("machine check simcheck on build-02") == 1, err
    finally:
        s.close()


def test_check_ref_picks_the_ref_the_sweeper_resolves(tmp_path, remote):
    presets = PRESETS.replace('repo = "app"\n', 'repo = "app"\ncheck_ref = "dev"\n')
    s = M5nServer(tmp_path, repo_url=remote.url, presets=presets)
    try:
        s.reported("build-02")
        [jid] = sweep(s)
        assert s.store.get_job(jid).source.sha == remote.dev
    finally:
        s.close()


def test_a_finished_check_is_not_a_duration_sample(srv):
    srv.reported("build-02")
    [jid] = sweep(srv)
    srv.clock.advance(1)
    assert srv.claimed("build-02") == jid
    srv.clock.advance(120)
    assert srv.finish("build-02", jid, "succeeded", exit_code=0)[0] == 200
    rows = srv.store.list_sample_rows(srv.clock.now - timedelta(days=1))
    assert [r.key for r in rows] == []


# ── 3. 다시 점검 (§8.1 · `rcm recheck`) ──────────────────────────────────────


def test_recheck_needs_an_admin_token(srv):
    srv.reported("build-02")
    status, body = srv.req("POST", "/machines/build-02/recheck", token="alice", json_body={})
    assert status == 403, body
    assert srv.req("POST", "/machines/build-02/recheck", json_body={})[0] == 401


def test_recheck_of_an_unknown_machine_is_404(srv):
    status, body = srv.req("POST", "/machines/nope/recheck", token="admin", json_body={})
    assert status == 404, body


@pytest.mark.parametrize("checks", [["ok"], ["nope"], "simcheck"])
def test_recheck_names_only_check_presets(srv, checks):
    srv.reported("build-02")
    status, body = srv.req(
        "POST", "/machines/build-02/recheck", token="admin", json_body={"checks": checks}
    )
    assert status == 400, body


def test_recheck_submits_again_even_after_the_automatic_one_failed(srv, remote):
    """자동 1회 제한과 무관하다 — 사람이 풀어 주는 길(§4.4)."""
    srv.reported("build-02")
    [auto] = sweep(srv)
    run_check(srv, auto, "failed")
    status, body = srv.req(
        "POST", "/machines/build-02/recheck", token="admin", json_body={"checks": ["simcheck"]}
    )
    assert status == 200, body
    assert [j["preset"] for j in body["jobs"]] == ["simcheck"]
    again = int(body["jobs"][0]["job_id"])
    job = srv.store.get_job(again)
    assert (job.target_worker, job.source.sha, job.sample) == ("build-02", remote.main, False)
    assert checks_of(srv)["simcheck"]["state"] == "running"
    assert checks_of(srv)["simcheck"]["auto_submitted"] is False
    rows = srv.store._conn().execute(
        "SELECT job_id, auto FROM machine_checks WHERE worker='build-02' ORDER BY submitted_at"
    )
    assert [(r["job_id"], r["auto"]) for r in rows] == [(auto, 1), (again, 0)]
    run_check(srv, again, "succeeded")
    assert verdict_of(srv, "scenario") == ("eligible", "ok")


def test_recheck_without_names_reruns_every_check_of_the_config(srv):
    srv.reported("build-02")
    status, body = srv.req("POST", "/machines/build-02/recheck", token="admin", json_body={})
    assert status == 200, body
    assert [j["preset"] for j in body["jobs"]] == ["simcheck"]
