"""M5r W · V: GitHub 쪽 대기 잡(`rcm_ci_wait.py wait`)과 판정(`verdict`).

docs/m5r-workplan.md §2.4 · §2.5 · §5.3 · §6 W · V. 가짜 GitHub 의 상태는 주입 시계가 `at` 에
닿아야 보인다. 대기 잡의 `sleep` 은 그 시계를 밀기만 하므로 30분 문턱도 진짜로 기다리지 않는다.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from ci_fakes import (
    HEAD_SHA,
    RCM_REPO_CI_TOML,
    T0,
    FakeClock,
    FakeGitHub,
    Status,
    actions_env,
    load_templates,
    outputs,
)

_cfg, waitmod, _watch, _status = load_templates()


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def gh(clock):
    g = FakeGitHub(clock)
    yield g
    g.close()


def write_cfg(tmp_path: Path, text: str = RCM_REPO_CI_TOML) -> Path:
    p = tmp_path / "repo" / ".rcm" / "ci.toml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def wait(tmp_path, gh, clock, *, text: str = RCM_REPO_CI_TOML, attempt: int = 1):
    cfg = write_cfg(tmp_path, text)
    env = actions_env(tmp_path / "gh", api=gh.url, attempt=attempt)
    rc = waitmod.main(["wait", "--config", str(cfg)], env=env, sleep=clock.sleep)
    return rc, outputs(env), env


def q(word: str, job: int | None, at: float, *, state: str = "pending", **kw) -> Status:
    text = f"{word} · #{job}" if job is not None else word
    return Status(state=state, description=text, at=at, **kw)


def status_reads(gh) -> list:
    return gh.calls("GET", f"/commits/{HEAD_SHA}/statuses")


# ── W1 · W2: 베스트 ──


def test_w1_queued_running_success_passes_with_the_job(tmp_path, gh, clock):
    gh.add_status(HEAD_SHA, q("queued", 12, T0 + 20))
    gh.add_status(HEAD_SHA, q("running", 12, T0 + 100))
    gh.add_status(HEAD_SHA, q("passed", 12, T0 + 400, state="success"))
    rc, out, _ = wait(tmp_path, gh, clock)
    assert rc == 0
    assert (out["result"], out["job"]) == ("pass", "12")
    assert gh.posted() == []
    assert all(s == 30 for s in clock.slept)  # poll_seconds 마다


def test_w2_a_failure_fails_and_is_not_taken_over(tmp_path, gh, clock):
    gh.add_status(HEAD_SHA, q("queued", 12, T0 + 10))
    gh.add_status(HEAD_SHA, q("failed", 12, T0 + 60, state="failure"))
    rc, out, env = wait(tmp_path, gh, clock)
    assert rc == 1
    assert (out["result"], out["job"]) == ("fail", "12")
    assert gh.posted() == []
    assert "12" in Path(env["GITHUB_STEP_SUMMARY"]).read_text()


# ── W3 ~ W6: 대체 ──


def test_w3_no_status_falls_back_after_pickup_without_taking_over(tmp_path, gh, clock):
    rc, out, _ = wait(tmp_path, gh, clock)
    assert rc == 0
    assert (out["result"], out["reason"]) == ("fallback", "no_pickup")
    assert 300 <= clock.now - T0 < 300 + 30
    assert gh.posted() == []


def test_w4_queued_too_long_posts_taken_over_once(tmp_path, gh, clock):
    gh.add_status(HEAD_SHA, q("queued", 12, T0))
    rc, out, _ = wait(tmp_path, gh, clock)
    assert rc == 0
    assert (out["result"], out["reason"], out["job"]) == ("fallback", "queued_too_long", "12")
    posted = gh.posted(HEAD_SHA)
    assert len(posted) == 1
    body = posted[0][1]
    assert body["state"] == "error" and body["context"] == "rcm/ci"
    assert body["description"].startswith("taken-over · GitHub Actions run 4242")
    assert "target_url" not in body or body["target_url"] in (None, "")
    assert 1800 <= clock.now - T0 < 1800 + 30
    assert gh.statuses[HEAD_SHA][-1].creator == "github-actions[bot]"


def test_w5_running_too_long_falls_back(tmp_path, gh, clock):
    gh.add_status(HEAD_SHA, q("queued", 12, T0))
    gh.add_status(HEAD_SHA, q("running", 12, T0 + 60))
    rc, out, _ = wait(tmp_path, gh, clock)
    assert (rc, out["result"], out["reason"]) == (0, "fallback", "run_too_long")
    assert 60 + 3600 <= clock.now - T0 < 60 + 3600 + 30
    assert len(gh.posted()) == 1


@pytest.mark.parametrize("word", ["unavailable", "superseded"])
def test_w6_an_error_status_falls_back_at_once_with_its_word(tmp_path, gh, clock, word):
    gh.add_status(HEAD_SHA, q("queued", 12, T0))
    gh.add_status(HEAD_SHA, q(word, 12, T0 + 30, state="error"))
    rc, out, _ = wait(tmp_path, gh, clock)
    assert (rc, out["result"], out["reason"]) == (0, "fallback", word)
    assert clock.now - T0 <= 60
    assert gh.posted() == []


# ── W7: 탈출구 ──


def test_w7_a_rerun_falls_back_without_calling_the_api(tmp_path, gh, clock):
    gh.add_status(HEAD_SHA, q("failed", 12, T0 - 600, state="failure"))
    rc, out, _ = wait(tmp_path, gh, clock, attempt=2)
    assert (rc, out["result"], out["reason"]) == (0, "fallback", "rerun")
    assert gh.requests == []
    assert clock.slept == []


# ── W8 · W9: API 오류 ──


def test_w8_errors_for_pickup_seconds_fall_back_as_api_error(tmp_path, gh, clock):
    gh.fail_always["/repos/monocsp/remote_ci_monitor/commits/"] = 500
    rc, out, _ = wait(tmp_path, gh, clock)
    assert (rc, out["result"], out["reason"]) == (0, "fallback", "api_error")
    assert 300 <= clock.now - T0 < 330


def test_w9_two_errors_then_success_passes(tmp_path, gh, clock):
    gh.fail["/repos/monocsp/remote_ci_monitor/commits/"] = [500, 500]
    gh.add_status(HEAD_SHA, q("passed", 12, T0, state="success"))
    rc, out, _ = wait(tmp_path, gh, clock)
    assert (rc, out["result"]) == (0, "pass")
    assert len(status_reads(gh)) == 3


# ── W10 ~ W13: 거르기 · 낯선 낱말 · ETag ──


def test_w10_only_statuses_from_the_listed_creators_count(tmp_path, gh, clock):
    gh.add_status(HEAD_SHA, q("passed", 66, T0 + 10, state="success", creator="mallory"))
    # 둘째 poll(T0+30)에서는 mallory 의 success 가 가장 새 상태다 — 그것을 믿으면 여기서 통과한다
    gh.add_status(HEAD_SHA, q("failed", 12, T0 + 50, state="failure", creator="monocsp"))
    rc, out, _ = wait(tmp_path, gh, clock)
    assert (rc, out["result"], out["job"]) == (1, "fail", "12")
    assert clock.now >= T0 + 50


def test_w11_another_context_is_ignored(tmp_path, gh, clock):
    gh.add_status(HEAD_SHA, Status("success", "passed", context="local-ci/full", at=T0))
    gh.add_status(HEAD_SHA, q("passed", 12, T0 + 90, state="success"))
    rc, out, _ = wait(tmp_path, gh, clock)
    assert (rc, out["result"], out["job"]) == (0, "pass", "12")
    assert clock.now >= T0 + 90


def test_w12_an_unknown_pending_word_counts_as_queued(tmp_path, gh, clock):
    gh.add_status(HEAD_SHA, q("warming", 5, T0))
    rc, out, _ = wait(tmp_path, gh, clock)
    assert (out["result"], out["reason"]) == ("fallback", "queued_too_long")
    assert 1800 <= clock.now - T0 < 1830  # run_seconds(3600) 가 아니다


def test_w13_unchanged_statuses_are_asked_with_if_none_match(tmp_path, gh, clock):
    gh.add_status(HEAD_SHA, q("queued", 12, T0))
    wait(tmp_path, gh, clock)
    reads = status_reads(gh)
    assert len(reads) > 10
    assert "if-none-match" not in reads[0].headers
    assert all("if-none-match" in r.headers for r in reads[1:])
    assert gh.not_modified >= len(reads) - 1


def test_w14_fallback_none_turns_no_pickup_into_a_failure(tmp_path, gh, clock):
    text = RCM_REPO_CI_TOML.replace('fallback = "github"', 'fallback = "none"')
    rc, out, _ = wait(tmp_path, gh, clock, text=text)
    assert (rc, out["result"], out["reason"]) == (1, "fail", "no_pickup")


@pytest.mark.parametrize("skew", [-7200, 7200])
def test_w15_the_runner_clock_does_not_matter(tmp_path, gh, clock, monkeypatch, skew):
    """경과는 상태의 `created_at` 과 응답의 `Date` 로 잰다. 러너의 시계를 2시간 비틀어도 같다."""
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + skew)
    gh.add_status(HEAD_SHA, q("queued", 12, T0))
    rc, out, _ = wait(tmp_path, gh, clock)
    assert (rc, out["result"], out["reason"]) == (0, "fallback", "queued_too_long")
    assert 1800 <= clock.now - T0 < 1830


def test_w16_an_existing_success_passes_on_the_first_request(tmp_path, gh, clock):
    gh.add_status(HEAD_SHA, q("passed", 12, T0 - 3600, state="success"))
    rc, out, _ = wait(tmp_path, gh, clock)
    assert (rc, out["result"]) == (0, "pass")
    assert len(status_reads(gh)) == 1
    assert clock.slept == []


# ── V: 판정 ────────────────────────────────────────────────────────────────


def needs(
    mode: str,
    *,
    route_result: str = "success",
    now: str = "",
    late: str = "",
    rcm: tuple[str, dict] = ("skipped", {}),
    now_job: tuple[str, str] = ("skipped", ""),
    late_job: tuple[str, str] = ("skipped", ""),
) -> dict:
    wait = "false" if mode == "github" else "true"
    return {
        "route": {
            "result": route_result,
            "outputs": {
                "mode": mode,
                "wait": wait,
                "now": now,
                "late": late,
                "context": "rcm/ci",
                "sha": HEAD_SHA,
                "ref": HEAD_SHA,
                "why": "default",
            },
        },
        "rcm": {"result": rcm[0], "outputs": rcm[1]},
        "now": {"result": now_job[0], "outputs": {"ran": now_job[1]} if now_job[1] else {}},
        "late": {"result": late_job[0], "outputs": {"ran": late_job[1]} if late_job[1] else {}},
    }


def verdict(tmp_path, gh, doc: dict, *, attempt: int = 1):
    cfg = write_cfg(tmp_path)
    env = actions_env(tmp_path / "v", api=gh.url, attempt=attempt, NEEDS_JSON=json.dumps(doc))
    rc = waitmod.main(["verdict", "--config", str(cfg)], env=env)
    return rc, Path(env["GITHUB_STEP_SUMMARY"]).read_text()


PASS = ("success", {"result": "pass", "reason": "", "job": "12"})
FAIL = ("failure", {"result": "fail", "reason": "", "job": "12"})
FALLBACK = ("success", {"result": "fallback", "reason": "queued_too_long", "job": "12"})
CRASHED = ("failure", {})

#: §2.5 판정 표의 아홉 줄
TABLE = {
    "github_now_success": (
        needs("github", now="ubuntu,macos", now_job=("success", "ubuntu,macos")),
        0,
        None,
    ),
    "rcm_pass": (needs("rcm", late="ubuntu", rcm=PASS), 0, None),
    "rcm_fail": (needs("rcm", late="ubuntu", rcm=FAIL), 1, "rcm #12 failed"),
    "rcm_fallback_late_success": (
        needs("rcm", late="ubuntu", rcm=FALLBACK, late_job=("success", "ubuntu")),
        0,
        "queued_too_long",
    ),
    "rcm_waiter_crashed": (
        needs("rcm", late="ubuntu", rcm=CRASHED, late_job=("success", "ubuntu")),
        0,
        "waiter crashed",
    ),
    "rcm_fallback_late_ran_empty": (
        needs("rcm", late="ubuntu", rcm=FALLBACK, late_job=("success", "")),
        1,
        "ubuntu",
    ),
    "both_pass": (
        needs("both", now="ubuntu,macos", rcm=PASS, now_job=("success", "ubuntu,macos")),
        0,
        None,
    ),
    "both_fallback": (
        needs("both", now="ubuntu,macos", rcm=FALLBACK, now_job=("success", "ubuntu,macos")),
        0,
        None,
    ),
    "both_fail": (
        needs("both", now="ubuntu,macos", rcm=FAIL, now_job=("success", "ubuntu,macos")),
        1,
        "rcm #12 failed",
    ),
    "route_failed": (
        needs("rcm", route_result="failure", late="ubuntu", rcm=PASS),
        1,
        "route",
    ),
}


@pytest.mark.parametrize("case", list(TABLE))
def test_verdict_table(tmp_path, gh, case):
    doc, want_rc, needle = TABLE[case]
    rc, summary = verdict(tmp_path, gh, doc)
    assert rc == want_rc, summary
    if needle:
        assert needle in summary, summary


def test_v1_fallback_with_late_skipped_fails(tmp_path, gh):
    rc, summary = verdict(tmp_path, gh, needs("rcm", late="ubuntu", rcm=FALLBACK))
    assert rc == 1, summary


def test_v2_fallback_with_late_success_but_nothing_ran_fails(tmp_path, gh):
    doc = needs("rcm", late="ubuntu", rcm=FALLBACK, late_job=("success", ""))
    rc, summary = verdict(tmp_path, gh, doc)
    assert rc == 1, summary
    assert "ubuntu" in summary


def _v3_doc() -> dict:
    return needs("rcm", late="ubuntu", rcm=FALLBACK, late_job=("success", "ubuntu"))


def test_v3_a_failure_that_raced_the_take_over_still_fails_the_first_attempt(tmp_path, gh):
    gh.add_status(HEAD_SHA, q("queued", 12, T0 - 1900))
    gh.add_status(HEAD_SHA, q("failed", 12, T0 - 5, state="failure"))
    gh.add_status(
        HEAD_SHA,
        Status(
            "error",
            "taken-over · GitHub Actions run 4242",
            creator="github-actions[bot]",
            at=T0 - 4,
        ),
    )
    rc, summary = verdict(tmp_path, gh, _v3_doc())
    assert rc == 1, summary
    assert "rcm #12 failed" in summary


def test_v4_the_same_on_a_rerun_passes(tmp_path, gh):
    gh.add_status(HEAD_SHA, q("failed", 12, T0 - 5, state="failure"))
    rc, summary = verdict(tmp_path, gh, _v3_doc(), attempt=2)
    assert rc == 0, summary


def test_v5_schedule_with_a_skipped_leg_fails_and_names_it(tmp_path, gh):
    doc = needs("github", now="ubuntu,macos", now_job=("success", "ubuntu"))
    rc, summary = verdict(tmp_path, gh, doc)
    assert rc == 1, summary
    assert "macos" in summary


# ── 격리 검증(#238)이 찾은 것 ─────────────────────────────────────────────────


def test_a_pending_status_without_a_readable_created_at_does_not_wait_forever(tmp_path, gh, clock):
    gh.add_status(HEAD_SHA, Status("pending", "queued · #12", at=T0, created_raw="yesterday"))
    rc, out, _ = wait(tmp_path, gh, clock)
    assert (rc, out["result"], out["reason"]) == (0, "fallback", "no_pickup")
    assert 300 <= clock.now - T0 < 330


def test_route_warnings_cannot_inject_workflow_commands(tmp_path, capsys):
    cfg = write_cfg(tmp_path)
    env = actions_env(tmp_path / "gh", mode="x\n::error::boom ::add-mask::y")
    assert waitmod.main(["route", "--legs", "ubuntu,macos", "--config", str(cfg)], env=env) == 0
    printed = capsys.readouterr().out
    lines = [ln for ln in printed.splitlines() if ln.startswith("::")]
    assert len(lines) == 1 and lines[0].startswith("::warning::")
    assert "::error::" not in printed and "::add-mask::" not in printed
    assert printed.count("::") == 2


def test_a_cut_off_error_body_does_not_escape(monkeypatch):
    """500 응답이 Content-Length 999 를 말하고 몇 바이트만 보낸 채 끊긴다. 본문을 읽다 나는
    `IncompleteRead` 가 새면 대기 잡 · 훅 · 감시가 트레이스백으로 죽는다. 셋 다 이 함수를 쓴다."""
    import socket
    import threading

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    port = srv.getsockname()[1]

    def serve():
        for _ in range(2):
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            conn.recv(65536)
            conn.sendall(
                b"HTTP/1.1 500 Internal Server Error\r\nContent-Type: application/json\r\n"
                b'Content-Length: 999\r\n\r\n{"mess'
            )
            conn.close()

    threading.Thread(target=serve, daemon=True).start()
    try:
        api = _cfg.GitHubApi(f"http://127.0.0.1:{port}", "monocsp/remote_ci_monitor", "t")
        reply = api.statuses(HEAD_SHA)
        assert reply.status == 500 and not reply.ok
        posted = api.post_status(HEAD_SHA, "error", "rcm/ci", "x")
        assert posted.status == 500 and not posted.ok
    finally:
        srv.close()
    for mod in (waitmod, _status, _watch):
        assert mod.cc.GitHubApi is _cfg.GitHubApi
