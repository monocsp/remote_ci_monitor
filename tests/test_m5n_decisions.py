"""M5n 오너 결정 105~108(2026-11-17) — 격리 검증이 남긴 넷에 대한 답.

- 105: 워커 토큰을 믿는 설계는 그대로. 다만 자동 점검은 (워커, 점검)마다 **24시간에 3번**까지 —
  지문을 매번 바꾸는 워커가 점검을 끝없이 돌리게 할 수 없다. 넘으면 `auto_check_limit`.
- 106: `rcm recheck` 도 `rcm run --machine` 처럼 down 머신은 409 다.
- 107: 판정 없이 끝난(취소 · 유실) 점검은 **아무것도 증명하지 않는다** — 같은 지문으로 이미 낸
  판정(통과 · 실패)을 지우지 않는다.
- 108: `--machine` · `recheck` 는 `rcm machines` 에 보이는 **표시 이름**도 받는다. 워커 id 가
  먼저고, 표시 이름이 여럿이면 400.
"""

from __future__ import annotations

import shutil

import pytest

from remote_ci_monitor.core import capability as cap
from test_checks_sweeper import check_jobs, run_check, sweep
from test_server_m5n import HOST_B, LOCAL, M5nServer, ok_probe

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")


@pytest.fixture
def remote(tmp_path):
    from gitrepo import build_remote

    root = tmp_path / "remote"
    root.mkdir()
    return build_remote(root)


@pytest.fixture
def srv(tmp_path, remote):
    s = M5nServer(tmp_path, repo_url=remote.url)
    yield s
    s.close()


# ── 105: 자동 점검 상한 ─────────────────────────────────────────────────────


def _flap(srv: M5nServer, gen: str, n: int) -> None:
    """지문을 바꿔 보고하고 한 회차 쓸고, 그 점검을 실패로 끝낸다(살아 있게 heartbeat 먼저)."""
    assert srv.heartbeat("build-02")[0] == 200
    assert srv.report("build-02", gen, probes={"xc": ok_probe(f"Xc {n}")})[0] == 200
    ids = sweep(srv)
    for jid in ids:
        run_check(srv, jid, "failed")


def test_auto_checks_stop_after_three_per_day_on_one_worker(srv):
    gen = srv.reported("build-02")
    [first] = sweep(srv)
    run_check(srv, first, "failed")
    _flap(srv, gen, 2)
    _flap(srv, gen, 3)
    assert len(check_jobs(srv)) == 3
    _flap(srv, gen, 4)  # 넷째 지문 — 하루 상한을 넘었다
    assert len(check_jobs(srv)) == 3
    check = {c["preset"]: c for c in srv.machines()["build-02"]["checks"]}["simcheck"]
    assert check["error_code"] == "auto_check_limit"


def test_the_limit_resets_after_a_day(srv):
    gen = srv.reported("build-02")
    [first] = sweep(srv)
    run_check(srv, first, "failed")
    _flap(srv, gen, 2)
    _flap(srv, gen, 3)
    srv.clock.advance(24 * 3600 + 1)
    srv.reported("build-02", probes={"xc": ok_probe("Xc 9")})
    assert len(sweep(srv)) == 1


def test_a_human_recheck_is_not_counted_and_not_limited(srv):
    gen = srv.reported("build-02")
    [first] = sweep(srv)
    run_check(srv, first, "failed")
    _flap(srv, gen, 2)
    _flap(srv, gen, 3)
    status, body = srv.req("POST", "/machines/build-02/recheck", token="admin", json_body={})
    assert status == 200, body
    assert isinstance(body["jobs"][0]["job_id"], int)


# ── 106: down 머신의 다시 점검은 409 ─────────────────────────────────────────


def test_recheck_of_a_down_machine_is_409(srv):
    srv.reported("build-02")
    srv.clock.advance(61)
    status, body = srv.req("POST", "/machines/build-02/recheck", token="admin", json_body={})
    assert status == 409, body
    assert "down" in body["error"] and "rcm worker" in body["error"]
    assert check_jobs(srv) == []


# ── 107: 판정 없이 끝난 점검은 앞선 판정을 지우지 않는다 ─────────────────────────


FP = "f" * 16


def rec(job_id: int, state: str) -> cap.CheckRecord:
    return cap.CheckRecord(preset="sim", job_id=job_id, job_state=state, fingerprint_hash=FP)


@pytest.mark.parametrize("ended", ["cancelled", "lost"])
def test_a_pass_survives_a_later_check_that_proved_nothing(ended):
    ok = rec(10, "succeeded")
    assert cap.check_state([ok, rec(11, ended)], FP) == (cap.CHECK_PASSED, ok)


@pytest.mark.parametrize("ended", ["cancelled", "lost"])
def test_a_failure_survives_a_later_check_that_proved_nothing(ended):
    bad = rec(10, "failed")
    assert cap.check_state([bad, rec(11, ended)], FP) == (cap.CHECK_FAILED, bad)


def test_a_running_check_after_a_pass_is_still_running():
    run = rec(11, "running")
    assert cap.check_state([rec(10, "succeeded"), run], FP) == (cap.CHECK_RUNNING, run)


def test_only_checks_that_proved_nothing_are_still_needed():
    lost = rec(11, "lost")
    assert cap.check_state([rec(10, "cancelled"), lost], FP) == (cap.CHECK_NEEDED, lost)


def test_a_lost_human_check_keeps_the_machine_open(srv):
    """서버 쪽 — 통과한 머신에 사람이 다시 건 점검이 유실돼도 그 머신은 그대로 받는다."""
    srv.reported("build-02")
    [first] = sweep(srv)
    run_check(srv, first, "succeeded")
    status, body = srv.req("POST", "/machines/build-02/recheck", token="admin", json_body={})
    again = int(body["jobs"][0]["job_id"])
    assert srv.claimed("build-02") == again
    srv.clock.advance(601)  # M5t-b: 잡은 timeout 이 아니라 끊김 유예(기본 600) 뒤에 닫힌다
    assert again in srv.app.mark_lost_workers(srv.clock.now)
    srv.reported("build-02")
    got = srv.machines()["build-02"]["presets"]["scenario"]
    assert (got["state"], got["code"]) == ("eligible", "ok")


# ── 108: 표시 이름으로도 고른다 ─────────────────────────────────────────────────


def test_machine_accepts_the_display_name(srv):
    srv.reported("build-02", host_name="b02-mini")
    status, body = srv.post_job("simcheck", machine="b02-mini")
    assert status == 201, body
    assert srv.store.get_job(body["job_id"]).target_worker == "build-02"


def test_machine_accepts_the_local_host_name(srv):
    name = srv.app.local_display_name()
    status, body = srv.post_job("simcheck", machine=name)
    assert status == 201, body
    assert srv.store.get_job(body["job_id"]).target_worker == LOCAL


def test_recheck_accepts_the_display_name(srv):
    srv.reported("build-02", host_name="b02-mini")
    status, body = srv.req("POST", "/machines/b02-mini/recheck", token="admin", json_body={})
    assert status == 200, body
    job = srv.store.get_job(int(body["jobs"][0]["job_id"]))
    assert job.target_worker == "build-02"


def test_a_worker_id_wins_over_a_display_name(srv):
    """워커 `mbp` 와, 표시 이름이 `mbp` 인 다른 워커 — id 가 먼저다."""
    srv.reported("mbp", pool="mbp", host_key=HOST_B)
    srv.reported("build-02", host_name="mbp")
    status, body = srv.post_job("simcheck", machine="mbp")
    assert status == 201, body
    assert srv.store.get_job(body["job_id"]).target_worker == "mbp"


def test_an_ambiguous_display_name_is_400_listing_the_ids(srv):
    srv.reported("build-02", host_name="twin")
    srv.reported("mac2-worker", host_name="twin")
    status, body = srv.post_job("simcheck", machine="twin")
    assert status == 400, body
    assert "build-02" in body["error"] and "mac2-worker" in body["error"]


def test_stale_records_do_not_leak_between_days(srv):
    """하루 창의 경계 — 24시간이 지난 자동 점검은 상한에 안 센다(경계 1초 전은 센다)."""
    gen = srv.reported("build-02")
    [first] = sweep(srv)
    run_check(srv, first, "failed")
    _flap(srv, gen, 2)
    srv.clock.advance(24 * 3600 - 1)
    _flap(srv, gen, 3)
    _flap(srv, gen, 4)
    assert len(check_jobs(srv)) == 3
    srv.clock.advance(2)
    _flap(srv, gen, 5)
    assert len(check_jobs(srv)) == 4
