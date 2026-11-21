"""M5r H: 빌드 머신의 감시 스크립트 `rcm_ci_watch.py` — 열린 PR 과 push 브랜치를 보고 잡을 낸다.

docs/m5r-workplan.md §2.6 · §5.4 · §6 H. GitHub 은 `FakeGitHub`, `rcm` 클라이언트는 argv 를 적는
가짜 실행 파일, 잡 조회(`GET /jobs/{id}`)는 `FakeRcm` 이다. 시계는 주입한다. 시간 제한 시험만
모듈 상수를 줄여 진짜 시간으로 잰다(그래도 몇 초 안에 끝난다).
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

from ci_fakes import (
    RCM_REPO_CI_TOML,
    REPO,
    T0,
    FakeClock,
    FakeGitHub,
    Status,
    fake_rcm,
    iso,
    load_templates,
    pr,
    submitted,
)
from fake_rcm_http import FakeRcm, free_port
from remote_ci_monitor.cli import main as cli_main
from remote_ci_monitor.core.model import Preset
from remote_ci_monitor.core.status import preset_json

cfgmod, _wait, watchmod, _status = load_templates()
GH_TOKEN = "gh-watch-token-value"
RCM_TOKEN = "rcm-watch-secret-value"
S1, S2, S3, S4 = ("1" * 40, "2" * 40, "3" * 40, "4" * 40)
HOST = socket.gethostname()


class Rig:
    def __init__(self, tmp: Path, capsys, *, branches: tuple[str, ...] = ()):
        self.tmp = tmp
        self.capsys = capsys
        self.clock = FakeClock()
        self.gh = FakeGitHub(self.clock)
        self.rcm_http = FakeRcm()
        self.rcm_http.route("GET", "/api/health", {"ok": True, "version": "0.8.0"})
        self.rcm_http.route("GET", "/api/whoami", {"name": "ci-watch", "admin": False})
        self.exe = fake_rcm(tmp / "bin")
        self.gh_token = tmp / "tokens" / "gh.token"
        self.rcm_token = tmp / "tokens" / "ci-watch.rcm"
        self.gh_token.parent.mkdir(parents=True)
        for path, value in ((self.gh_token, GH_TOKEN), (self.rcm_token, RCM_TOKEN)):
            path.write_text(value + "\n")
            path.chmod(0o600)
        self.state_dir = tmp / "state"
        self.log_file = tmp / "logs" / "ci-watch.log"
        self.config = tmp / "ci-watch.toml"
        self.write_config(branches)
        self.printed = ""

    def write_config(
        self,
        branches: tuple[str, ...],
        *,
        legs: bool = True,
        job_url: bool = False,
        repos_before: str = "",
        rcm_token_line: str | None = None,
        github_token_line: str | None = None,
    ) -> None:
        # 토큰 줄을 주면 기본 줄(토큰 파일) 대신 그 줄을 쓴다 — 빈 문자열이면 그 키가 없다
        rcm_line = (
            f'rcm_token_file = "{self.rcm_token}"\n' if rcm_token_line is None else rcm_token_line
        )
        gh_line = (
            f'github_token_file = "{self.gh_token}"\n'
            if github_token_line is None
            else github_token_line
        )
        self.config.write_text(
            f'rcm = "{self.exe.path}"\n'
            f'server = "{self.rcm_http.url}"\n' + rcm_line + f'state_dir = "{self.state_dir}"\n'
            f'log_file = "{self.log_file}"\n'
            f"job_url = {'true' if job_url else 'false'}\n"
            f'github_api = "{self.gh.url}"\n\n' + repos_before + "[[repos]]\n"
            f'github = "{REPO}"\n'
            + gh_line
            + f"branches = {json.dumps(list(branches))}\n"
            + ('legs = ["ubuntu", "macos"]\n' if legs else "")
        )

    def add_pr(self, number: int, sha: str, branch: str, *, toml: str = RCM_REPO_CI_TOML, **kw):
        self.gh.pulls.append(pr(number, sha, branch, **kw))
        if toml is not None:
            self.gh.files[(sha, ".rcm/ci.toml")] = toml

    def set_branch(self, name: str, sha: str, *, toml: str = RCM_REPO_CI_TOML) -> None:
        self.gh.branches[name] = sha
        self.gh.files[(sha, ".rcm/ci.toml")] = toml

    def job(self, job_id: int, **doc) -> None:
        self.rcm_http.route("GET", f"/jobs/{job_id}", {"id": job_id, **doc})

    def run(self, *extra: str, mono_fn=None) -> int:
        rc = watchmod.main(
            ["--config", str(self.config), "--once", *extra], now_fn=self.clock, mono_fn=mono_fn
        )
        cap = self.capsys.readouterr()
        self.printed += cap.out + cap.err
        self.assert_no_secret()
        return rc

    def assert_no_secret(self) -> None:
        # H16 — 로그와 출력 어디에도 토큰 문자열이 없다
        text = self.printed + self.log()
        assert GH_TOKEN not in text and RCM_TOKEN not in text

    def log(self) -> str:
        return self.log_file.read_text() if self.log_file.exists() else ""

    def posts(self, sha: str | None = None) -> list[dict]:
        return [body for _s, body in self.gh.posted(sha)]

    def close(self) -> None:
        self.gh.close()
        self.rcm_http.close()


@pytest.fixture
def rig(tmp_path, capsys):
    r = Rig(tmp_path, capsys)
    yield r
    r.close()


# ── H1 · H2 · H3: 베스트 ──


def test_h1_a_same_repo_pr_is_submitted_once_and_marked_queued(rig):
    rig.add_pr(7, S1, "feat/x")
    rig.exe.set(run={"stdout": submitted(12, S1)})
    assert rig.run() == 0
    assert rig.exe.argvs("run") == [
        [
            "run", "rcm-ci", "--source", "git_ref", "--ref", "feat/x", "--no-wait",
            "--by", f"ci-watch@{HOST}", "--priority", "low",
            "--link", f"https://github.test/{REPO}/pull/7", "--link-label", "PR #7",
        ]
    ]  # fmt: skip
    call = rig.exe.calls()[0]
    assert call["server"] == rig.rcm_http.url and call["has_token"]
    posts = rig.posts(S1)
    assert len(posts) == 1
    assert posts[0]["state"] == "pending" and posts[0]["context"] == "rcm/ci"
    assert posts[0]["description"].startswith("queued · #12")
    assert not posts[0].get("target_url")
    assert "submit" in rig.log() and "post queued" in rig.log()


def test_h2_running_is_posted_once(rig):
    rig.add_pr(7, S1, "feat/x")
    rig.exe.set(run={"stdout": submitted(12, S1)})
    rig.job(12, state="queued", reason=None)
    rig.run()
    rig.job(12, state="running", reason=None)
    rig.clock.advance(60)
    rig.run()
    rig.clock.advance(60)
    rig.run()
    words = [p["description"].split(" · ")[0] for p in rig.posts(S1)]
    assert words == ["queued", "running"]
    assert rig.posts(S1)[1]["description"] == "running · #12"
    assert len(rig.exe.argvs("run")) == 1


def test_h3_a_listed_push_branch_is_submitted_and_main_is_not(rig):
    rig.write_config(("dev", "main"))
    rig.set_branch("dev", S2)
    rig.set_branch("main", S3)
    rig.exe.set(run={"stdout": submitted(20, S2)})
    rig.run()
    runs = rig.exe.argvs("run")
    assert len(runs) == 1
    assert runs[0][runs[0].index("--ref") + 1] == "dev"
    assert "--link" not in runs[0]
    assert rig.posts(S3) == [] and len(rig.posts(S2)) == 1


# ── H4 · H5: 남의 코드와 손잡이 ──


def test_h4_forks_deleted_forks_and_dependabot_are_never_submitted(rig):
    rig.add_pr(1, S1, "evil", head_repo="mallory/remote_ci_monitor", author="mallory")
    rig.add_pr(2, S2, "gone", head_repo=None, author="someone")
    rig.add_pr(3, S3, "dependabot/pip/x", author="dependabot[bot]")
    rig.exe.set(run={"stdout": submitted(1, S1)})
    rig.run()
    assert rig.exe.argvs("run") == []
    assert rig.gh.posted() == []


def test_h5_the_repository_variable_github_stops_submissions(rig):
    rig.gh.variables["RCM_CI_MODE"] = "github"
    rig.add_pr(7, S1, "feat/x")
    rig.exe.set(run={"stdout": submitted(12, S1)})
    rig.run()
    assert rig.exe.argvs("run") == []
    assert rig.gh.posted() == []


# ── H6 · H7 · H23: 제출이 실패한다 ──


def real_cli_failure(case: str, monkeypatch, capsys) -> tuple[int, str]:
    """진짜 `rcm run … --no-wait` 를 돌려 그 종료 코드와 stderr 첫 줄을 얻는다.

    가짜 rcm 에 손으로 쓴 문장을 넣으면 CLI 의 문장이 바뀌어도 시험이 모른다(격리 검증 1:
    「token rejected …」 가 `submit_failed` 로 갔다). 서버는 `FakeRcm` 이 흉내 낸다."""
    srv = FakeRcm()
    try:
        doc = preset_json(Preset(name="rcm-ci", argv=("true",), source_modes=("git_ref",)))
        srv.route("GET", "/api/status", {"presets": [doc]})
        srv.route("GET", "/api/health", {"ok": True, "version": "0.7.0"})
        srv.route("GET", "/api/whoami", {"name": "ci-watch", "admin": False})
        url, preset = srv.url, "rcm-ci"
        if case == "server":
            url = f"http://127.0.0.1:{free_port()}"
        elif case == "preset":
            preset = "gate"
        elif case == "token":
            srv.route("GET", "/api/whoami", {"error": "a valid bearer token is required"}, 401)
        monkeypatch.setenv("RCM_SERVER", url)
        monkeypatch.setenv("RCM_TOKEN", "revoked-token-value")
        capsys.readouterr()
        try:
            rc = cli_main(["run", preset, "--source", "git_ref", "--ref", "feat/x", "--no-wait"])
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 2
        err = capsys.readouterr().err
    finally:
        srv.close()
    return rc, err.strip().splitlines()[0]


@pytest.mark.parametrize(
    ("case", "code"),
    [("preset", "unknown_preset"), ("token", "rcm_auth")],
    ids=["h7_preset", "h23_rcm_token"],
)
def test_h7_h23_a_failed_submit_posts_unavailable_with_a_code(rig, monkeypatch, capsys, case, code):
    """설정 · 인증 실패는 다시 내도 같다 — 곧바로 error 를 올리고 다시 내지 않는다."""
    rc, stderr = real_cli_failure(case, monkeypatch, capsys)
    assert rc == 2 and stderr.startswith("rcm: "), (rc, stderr)
    assert watchmod.submit_reason(rc, stderr) == code
    rig.add_pr(7, S1, "feat/x")
    rig.exe.set(run={"rc": rc, "stderr": stderr})
    rig.run()
    posts = rig.posts(S1)
    assert len(posts) == 1
    assert (posts[0]["state"], posts[0]["description"]) == ("error", f"unavailable · {code}")
    rig.clock.advance(60)
    rig.run()
    assert len(rig.exe.argvs("run")) == 1  # 다룬 sha 는 다시 내지 않는다


# ── H8 · H9: 이미 있는 상태 · 바뀐 sha ──


def test_h8_an_existing_status_means_someone_handled_it(rig):
    rig.add_pr(7, S1, "feat/x")
    rig.gh.add_status(S1, Status("success", "passed · #3", at=T0 - 600))
    rig.exe.set(run={"stdout": submitted(12, S1)})
    rig.run()
    assert rig.exe.argvs("run") == []
    assert rig.gh.posted() == []


def test_h9_the_status_goes_to_the_sha_the_server_resolved(rig):
    rig.add_pr(7, S1, "feat/x")
    rig.exe.set(run={"stdout": submitted(12, S2)})
    rig.job(12, **ours(state="queued", reason=None))  # 진짜 잡 응답 — 없으면 job() 이 None 이다
    rig.run()
    assert rig.posts(S1) == []
    assert [p["description"].split(" · ")[0] for p in rig.posts(S2)] == ["queued"]
    assert rig.exe.argvs("cancel") == []


# ── H10 · H11 · H12: 따라가기 ──


@pytest.mark.parametrize(("reason", "limit"), [("worker_down", 120), ("paused", 300)])
def test_h10_a_stuck_reason_is_reported_and_the_job_cancelled(rig, reason, limit):
    rig.add_pr(7, S1, "feat/x")
    rig.exe.set(run={"stdout": submitted(12, S1)})
    rig.job(12, state="queued", reason=reason)
    rig.run()
    rig.clock.advance(limit - 10)
    rig.run()
    assert len(rig.posts(S1)) == 1 and rig.exe.argvs("cancel") == []
    rig.clock.advance(11)
    rig.run()
    last = rig.posts(S1)[-1]
    assert (last["state"], last["description"]) == ("error", f"unavailable · #12 · {reason}")
    assert rig.exe.argvs("cancel") == [["cancel", "12"]]


def test_h11_taken_over_cancels_the_queued_job(rig):
    rig.add_pr(7, S1, "feat/x")
    rig.exe.set(run={"stdout": submitted(12, S1)})
    rig.job(12, state="queued", reason=None)
    rig.run()
    rig.clock.advance(1800)
    rig.gh.add_status(
        S1,
        Status(
            "error",
            "taken-over · GitHub Actions run 1",
            creator="github-actions[bot]",
            at=rig.clock.now,
        ),
    )
    rig.run()
    assert rig.exe.argvs("cancel") == [["cancel", "12"]]
    assert "taken-over" in rig.log()
    rig.clock.advance(60)
    rig.run()
    assert len(rig.exe.argvs("cancel")) == 1


@pytest.mark.parametrize(("ago", "posted"), [(121, True), (30, False)])
def test_h12_a_missed_hook_is_covered_after_120_seconds(rig, ago, posted):
    rig.add_pr(7, S1, "feat/x")
    rig.exe.set(run={"stdout": submitted(12, S1)})
    rig.job(12, state="queued", reason=None)
    rig.run()
    rig.clock.advance(600)
    rig.job(
        12, state="failed", exit_code=1, failed_step="test", last_step="test",
        summary="1 failed", cancelled_by=None, superseded_by=None,
        finished_at=iso(rig.clock.now - ago),
    )  # fmt: skip
    rig.run()
    last = rig.posts(S1)[-1]
    if posted:
        assert (last["state"], last["description"]) == ("failure", "failed · #12 · test")
    else:
        assert last["description"].startswith("queued")


# ── H13 · H14 · H18: 잠금과 상태 파일 ──


def lock_path(rig) -> Path:
    return rig.state_dir / "monocsp__remote_ci_monitor.lock"


def test_h13_a_held_lock_makes_the_second_process_exit_0(rig):
    rig.add_pr(7, S1, "feat/x")
    rig.exe.set(run={"stdout": submitted(12, S1)})
    rig.state_dir.mkdir(parents=True)
    lock_path(rig).write_text(json.dumps({"pid": 1, "at": rig.clock.now - 60}))
    assert rig.run() == 0
    assert rig.gh.requests == [] and rig.exe.calls() == []


def test_h13_two_processes_at_once_only_one_runs(rig):
    """진짜 동시성 — 시간 문턱이 아니라 사건으로 순서를 정한다.

    앞 프로세스(A)는 저장소 변수 요청에서 붙잡힌다. 가짜 GitHub 이 그 요청을 받았다는 것은 A 가
    이미 잠금을 쥐었다는 뜻이다(잠금 → 상태 파일 → 요청 순서). 그때 뒤 프로세스(B)를 띄우면 B 는
    GitHub 에도 rcm 에도 아무것도 하지 않고 0 으로 끝나야 한다. 그다음 A 를 풀어 준다."""
    rig.add_pr(7, S1, "feat/x")
    rig.exe.set(run={"stdout": submitted(12, S1)})
    rig.gh.hang.add(f"/repos/{REPO}/actions/")
    first = threading.Thread(
        target=watchmod.main, args=(["--config", str(rig.config), "--once"],),
        kwargs={"now_fn": rig.clock},
    )  # fmt: skip
    first.start()
    try:
        assert rig.gh.arrived.wait(30), "A never reached GitHub"
        assert lock_path(rig).exists()
        seen = len(rig.gh.requests)
        assert watchmod.main(["--config", str(rig.config), "--once"], now_fn=rig.clock) == 0
        assert len(rig.gh.requests) == seen  # B 는 GitHub 에 묻지 않았다
        assert rig.exe.calls() == []  # B 는 rcm 을 부르지 않았다
    finally:
        rig.gh.hang.clear()
        rig.gh.release.set()
        first.join(30)
    assert not first.is_alive()
    assert len(rig.exe.argvs("run")) == 1
    assert not lock_path(rig).exists()  # A 가 잠금을 놓았다


@pytest.mark.parametrize(("age", "steals"), [(660, True), (540, False)])
def test_h18_a_lock_older_than_10_minutes_is_taken(rig, age, steals):
    assert watchmod.LOCK_STALE_SECONDS == 600
    rig.add_pr(7, S1, "feat/x")
    rig.exe.set(run={"stdout": submitted(12, S1)})
    rig.state_dir.mkdir(parents=True)
    lock_path(rig).write_text(json.dumps({"pid": 1, "at": rig.clock.now - age}))
    assert rig.run() == 0
    assert (len(rig.exe.argvs("run")) == 1) is steals
    assert ("lock stale" in rig.log()) is steals


def test_h14_a_corrupt_state_file_is_moved_aside(rig):
    rig.add_pr(7, S1, "feat/x")
    rig.gh.add_status(S1, Status("pending", "queued · #12", at=T0 - 30))
    rig.exe.set(run={"stdout": submitted(12, S1)})
    rig.state_dir.mkdir(parents=True)
    (rig.state_dir / "monocsp__remote_ci_monitor.json").write_text("{not json")
    assert rig.run() == 0
    assert list(rig.state_dir.glob("monocsp__remote_ci_monitor.json.corrupt*"))
    json.loads((rig.state_dir / "monocsp__remote_ci_monitor.json").read_text())
    assert rig.exe.argvs("run") == []  # H8 이 막는다


# ── H15 · H22: 토큰 ──


def test_h15_an_open_token_file_fails_check_and_stops_the_run(rig):
    rig.gh_token.chmod(0o644)
    rig.add_pr(7, S1, "feat/x")
    assert rig.run("--check") != 0
    fails = [ln for ln in rig.printed.splitlines() if ln.startswith("FAIL")]
    assert any("0644" in ln for ln in fails), rig.printed
    assert watchmod.main(["--config", str(rig.config), "--once"], now_fn=rig.clock) == 2
    rig.capsys.readouterr()
    assert "0644" in rig.log()
    assert rig.exe.argvs("run") == [] and rig.gh.posted() == []


def test_h15_check_is_all_ok_on_a_good_setup(rig):
    assert rig.run("--check") == 0, rig.printed
    assert not [ln for ln in rig.printed.splitlines() if ln.startswith("FAIL")]


def test_h22_an_expired_github_token_logs_one_line_per_round(rig):
    rig.add_pr(7, S1, "feat/x")
    rig.exe.set(run={"stdout": submitted(12, S1)})
    rig.gh.auth_fail = True
    rig.run()
    assert rig.log().count("github_auth") == 1
    rig.clock.advance(60)
    rig.run()
    assert rig.log().count("github_auth") == 2
    assert rig.exe.calls() == [] and rig.gh.posted() == []
    rig.run("--check")
    fails = [ln for ln in rig.printed.splitlines() if ln.startswith("FAIL")]
    assert any("github" in ln.lower() and "401" in ln for ln in fails), rig.printed


# ── H17: 시간 제한 ──


def test_h17_the_limits_are_the_spec_values():
    assert cfgmod.HTTP_TIMEOUT == 15
    assert watchmod.RCM_TIMEOUT == 60
    assert watchmod.ROUND_SECONDS == 50


def test_h17_a_silent_github_request_skips_only_that_work(rig, monkeypatch):
    monkeypatch.setattr(cfgmod, "HTTP_TIMEOUT", 0.3)
    rig.add_pr(7, S1, "feat/x")
    rig.add_pr(8, S2, "feat/y")
    rig.gh.hang.add(f"/repos/{REPO}/contents/.rcm/ci.toml?ref={S1}")
    rig.exe.set(run=[{"stdout": submitted(13, S2)}, {"stdout": submitted(12, S1)}])
    started = time.monotonic()
    rig.run()
    assert time.monotonic() - started < 3
    runs = rig.exe.argvs("run")
    assert [a[a.index("--ref") + 1] for a in runs] == ["feat/y"]
    assert rig.posts(S1) == [] and len(rig.posts(S2)) == 1
    rig.gh.hang.clear()
    rig.clock.advance(60)
    rig.run()
    assert len(rig.exe.argvs("run")) == 2  # 건너뛴 일은 다음 바퀴가 한다
    assert len(rig.posts(S1)) == 1


def test_h17_a_stuck_rcm_is_cut_off(rig, monkeypatch):
    monkeypatch.setattr(watchmod, "RCM_TIMEOUT", 0.5)
    rig.add_pr(7, S1, "feat/x")
    rig.exe.set(run={"stdout": submitted(12, S1), "sleep": 5})
    started = time.monotonic()
    rig.run()
    assert time.monotonic() - started < 3
    # 끊긴 제출은 서버 재시작 순간과 같다 — error 가 아니라 다음 바퀴에 다시 낸다
    assert [(p["state"], p["description"]) for p in rig.posts(S1)] == [
        ("pending", "queued · retrying submit (1/3)")
    ]
    assert "timeout" in rig.log()


class Mono:
    """주입 단조 시계 — 읽을 때마다 `step` 초씩 간다(한 바퀴 50초를 진짜로 쓰지 않는다)."""

    def __init__(self, step: float):
        self.t, self.step = 1000.0, step

    def __call__(self) -> float:
        self.t += self.step
        return self.t


def test_h17_a_round_stops_at_its_limit(rig):
    for n in range(10):
        rig.add_pr(10 + n, f"{n:x}" * 40, f"feat/{n}")
    rig.exe.set(run={"stdout": submitted(12, S1)})
    assert rig.run(mono_fn=Mono(4.0)) == 0
    assert "round deadline" in rig.log()
    assert 0 < len(rig.exe.argvs("run")) < 10  # 시한 앞의 일은 했고 뒤의 일은 다음 바퀴로


# ── H19 · H20 · H21 ──


def test_h19_an_unreadable_config_posts_watcher_config(rig):
    rig.add_pr(7, S1, "feat/x")
    rig.gh.fail_always[f"/repos/{REPO}/contents/"] = 500
    rig.exe.set(run={"stdout": submitted(12, S1)})
    rig.run()
    assert rig.exe.argvs("run") == []
    posts = rig.posts(S1)
    assert [(p["state"], p["description"]) for p in posts] == [
        ("error", "unavailable · watcher_config")
    ]


def test_h20_no_repository_variable_follows_the_file(rig):
    rig.add_pr(7, S1, "feat/x")
    rig.exe.set(run={"stdout": submitted(12, S1)})
    rig.run()
    assert rig.gh.calls("GET", "/actions/variables/RCM_CI_MODE")
    assert len(rig.exe.argvs("run")) == 1


def test_h21_joining_a_session_job_follows_that_job(rig):
    rig.add_pr(7, S1, "feat/x")
    rig.exe.set(run={"stdout": submitted(5, S1, joined=True, position=None)})
    rig.job(5, state="running", reason=None)
    rig.run()
    posts = rig.posts(S1)
    assert posts[0]["description"].startswith("queued · #5")
    assert any(r.path == "/jobs/5" for r in rig.rcm_http.requests)
    assert "joined" in rig.log()


# ── H24 · H25: 새 커밋과 squash ──


def test_h24_a_new_head_is_submitted_and_the_old_record_is_kept(rig):
    rig.add_pr(7, S1, "feat/x")
    rig.exe.set(run=[{"stdout": submitted(12, S1)}, {"stdout": submitted(13, S2)}])
    rig.run()
    rig.gh.pulls.clear()
    rig.add_pr(7, S2, "feat/x")
    rig.clock.advance(60)
    rig.run()
    assert len(rig.exe.argvs("run")) == 2
    assert len(rig.posts(S1)) == 1 and len(rig.posts(S2)) == 1
    state = json.loads((rig.state_dir / "monocsp__remote_ci_monitor.json").read_text())
    assert state["shas"][S1]["job"] == 12 and state["shas"][S2]["job"] == 13


def test_h25_a_squashed_dev_head_is_submitted_on_its_own(rig):
    rig.write_config(("dev",))
    rig.add_pr(7, S1, "feat/x")
    rig.set_branch("dev", S4)
    rig.exe.set(run=[{"stdout": submitted(12, S1)}, {"stdout": submitted(14, S4)}])
    rig.run()
    refs = sorted(a[a.index("--ref") + 1] for a in rig.exe.argvs("run"))
    assert refs == ["dev", "feat/x"]
    assert len(rig.posts(S1)) == 1 and len(rig.posts(S4)) == 1


# ── 기록 정리 ──


def test_records_older_than_14_days_are_dropped(rig):
    rig.add_pr(7, S1, "feat/x")
    rig.exe.set(run={"stdout": submitted(12, S1)})
    rig.run()
    rig.gh.pulls.clear()
    rig.clock.advance(15 * 86400)
    rig.run()
    state = json.loads((rig.state_dir / "monocsp__remote_ci_monitor.json").read_text())
    assert S1 not in state["shas"]


def test_the_state_file_is_private(rig):
    rig.add_pr(7, S1, "feat/x")
    rig.exe.set(run={"stdout": submitted(12, S1)})
    rig.run()
    mode = os.stat(rig.state_dir / "monocsp__remote_ci_monitor.json").st_mode & 0o777
    assert mode & 0o077 == 0


# ── 격리 검증(#238)이 찾은 것 ─────────────────────────────────────────────────


def test_job_url_true_links_the_rcm_job_and_the_default_does_not(rig):
    rig.write_config((), job_url=True)
    rig.add_pr(7, S1, "feat/x")
    rig.exe.set(run={"stdout": submitted(12, S1)})
    rig.job(12, state="running", reason=None)
    rig.run()
    posts = rig.posts(S1)
    assert [p["description"].split(" · ")[0] for p in posts] == ["queued", "running"]
    # 화면 링크다 — `/jobs/12` 는 JSON 이다(서버의 job_url() 과 같은 꼴)
    assert all(p["target_url"] == f"{rig.rcm_http.url}/#/jobs/12" for p in posts)


def test_a_broken_repository_does_not_stop_the_next_one(rig, tmp_path):
    empty = tmp_path / "tokens" / "empty.token"
    empty.write_text("")
    empty.chmod(0o600)
    rig.write_config(
        (),
        repos_before=(
            f'[[repos]]\ngithub = "monocsp/other"\ngithub_token_file = "{empty}"\nbranches = []\n\n'
        ),
    )
    rig.add_pr(7, S1, "feat/x")
    rig.exe.set(run={"stdout": submitted(12, S1)})
    assert rig.run() == 0
    assert len(rig.exe.argvs("run")) == 1
    assert "monocsp/other" in rig.log() and "ValueError" in rig.log()


def test_an_incomplete_read_is_a_network_error(monkeypatch):
    import http.client
    import urllib.request

    def boom(*_a, **_k):
        raise http.client.IncompleteRead(b"par")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    reply = cfgmod.GitHubApi("http://127.0.0.1:9", REPO, "t").statuses(S1)
    assert reply.status == 0 and not reply.ok


def test_cancel_of_a_joined_job_only_leaves_the_join(rig):
    rig.add_pr(7, S1, "feat/x")
    rig.exe.set(
        run={"stdout": submitted(5, S1, joined=True, position=None)},
        cancel={"stdout": {"job_id": 5, "left": True}},
    )
    rig.job(5, state="queued", reason=None)
    rig.run()
    rig.clock.advance(1800)
    rig.gh.add_status(
        S1,
        Status(
            "error",
            "taken-over · GitHub Actions run 1",
            creator="github-actions[bot]",
            at=rig.clock.now,
        ),
    )
    rig.run()
    assert rig.exe.argvs("cancel") == [["cancel", "5"]]
    assert "left join of #5 (job keeps running)" in rig.log()
    assert "cancel #5" not in rig.log()


def test_without_legs_check_warns_and_the_round_logs_it(rig):
    rig.write_config((), legs=False)
    assert rig.run("--check") == 0
    warns = [ln for ln in rig.printed.splitlines() if ln.startswith("WARN")]
    assert any("legs" in ln for ln in warns), rig.printed
    rig.add_pr(7, S1, "feat/x")
    rig.exe.set(run={"stdout": submitted(12, S1)})
    rig.run()
    assert rig.log().count("legs") == 1


@pytest.mark.parametrize(
    "headers",
    [
        {"Retry-After": "120"},
        {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(T0) + 120)},
    ],
    ids=["retry_after", "ratelimit_reset"],
)
def test_a_rate_limit_rests_the_repository_until_the_reset(rig, headers):
    rig.add_pr(7, S1, "feat/x")
    rig.exe.set(run={"stdout": submitted(12, S1)})
    rig.gh.fail[f"/repos/{REPO}/actions/"] = [403 if "X-RateLimit-Reset" in headers else 429]
    rig.gh.fail_headers = headers
    rig.run()
    assert "rate_limited" in rig.log() and "github_auth" not in rig.log()
    rig.gh.requests.clear()
    rig.clock.advance(60)
    rig.run()
    assert rig.gh.requests == []  # 쉬는 동안은 묻지도 않는다
    rig.clock.advance(61)
    rig.run()
    assert len(rig.exe.argvs("run")) == 1


def test_a_rate_limited_config_read_waits_instead_of_posting_watcher_config(rig):
    rig.add_pr(7, S1, "feat/x")
    rig.exe.set(run={"stdout": submitted(12, S1)})
    rig.gh.fail[f"/repos/{REPO}/contents/"] = [429]
    rig.gh.fail_headers = {"Retry-After": "30"}
    rig.run()
    assert rig.gh.posted() == [] and rig.exe.argvs("run") == []
    rig.clock.advance(60)
    rig.run()
    assert len(rig.exe.argvs("run")) == 1
    assert [p["description"].split(" · ")[0] for p in rig.posts(S1)] == ["queued"]


def test_the_job_lookup_carries_the_rcm_token(rig):
    rig.add_pr(7, S1, "feat/x")
    rig.exe.set(run={"stdout": submitted(12, S1)})
    rig.job(12, state="queued", reason=None)
    rig.run()
    seen = [r for r in rig.rcm_http.requests if r.path == "/jobs/12"]
    assert seen and seen[0].headers.get("authorization") == f"Bearer {RCM_TOKEN}"


# ── R15: 이 레포의 운영 형태 — GitHub 대체 없이 rcm 만 (docs/m5r-workplan.md §13) ──
#
# 이 레포는 GitHub 대체를 쓰지 않는다(`fallback = "none"`). 잡 스크립트(운영의 rcm-ci.sh)가
# 시작에 자기 pending 을, 끝에 결과를 직접 올린다. 감시는 내고 `queued` 를 올리고, 잡이 결과를
# 못 올린 경우(취소 · 시간 초과 · 유실)만 덮는다. 토큰은 새 파일 없이 기존 자리에서 읽는다.

ROOT = Path(__file__).resolve().parents[1]


def repo_ci_toml() -> str:
    """레포 루트의 `.rcm/ci.toml` — 감시가 이 레포의 커밋에서 읽을 그 파일."""
    return (ROOT / ".rcm" / "ci.toml").read_text(encoding="utf-8")


def fake_gh(folder: Path, *, token: str = GH_TOKEN, rc: int = 0) -> Path:
    """`gh auth token --user <login>` 흉내. 인자를 파일에 적고 토큰 한 줄을 찍는다."""
    folder.mkdir(parents=True, exist_ok=True)
    exe = folder / "gh"
    exe.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        f"open({str(folder / 'gh.argv')!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
        f"if {rc}:\n"
        "    sys.stderr.write('no oauth token found for monocsp\\n')\n"
        f"    sys.exit({rc})\n"
        f"print({token!r})\n"
    )
    exe.chmod(0o755)
    return exe


def test_r15_the_repository_file_is_rcm_without_a_fallback():
    cfg = cfgmod.parse(repo_ci_toml(), legs=None)
    assert (cfg.mode, cfg.fallback) == ("rcm", "none")
    assert cfg.rcm is not None
    assert (cfg.rcm.preset, cfg.rcm.context) == ("rcm-ci", "rcm/ci")
    assert cfg.rcm.push == ("dev",) and cfg.rcm.creators == ("monocsp",)
    route = cfgmod.resolve(
        cfg, event="pull_request", base="dev", fork=False, override=None, legs=()
    )
    assert route.wait and route.fallback == "none" and route.late == ()
    dev = cfgmod.resolve(cfg, event="push", base="dev", fork=False, override=None, legs=())
    main = cfgmod.resolve(cfg, event="push", base="main", fork=False, override=None, legs=())
    assert dev.wait and not main.wait


def test_r15_the_job_scripts_own_pending_is_not_overwritten_with_running(rig):
    rig.add_pr(7, S1, "feat/x", toml=repo_ci_toml())
    rig.exe.set(run={"stdout": submitted(12, S1)})
    rig.job(12, state="queued", reason=None)
    rig.run()
    rig.clock.advance(60)
    rig.job(12, state="running", reason=None)
    rig.gh.add_status(S1, Status("pending", "rcm job 12 running", at=rig.clock.now))
    rig.run()
    rig.clock.advance(60)
    rig.run()
    assert [p["description"].split(" · ")[0] for p in rig.posts(S1)] == ["queued"]


@pytest.mark.parametrize(
    ("state", "text"), [("success", "rcm job 12 passed"), ("failure", "rcm job 12 failed at ruff")]
)
def test_r15_a_result_posted_before_the_job_ends_is_never_covered_by_pending(rig, state, text):
    rig.add_pr(7, S1, "feat/x", toml=repo_ci_toml())
    rig.exe.set(run={"stdout": submitted(12, S1)})
    rig.job(12, state="queued", reason=None)
    rig.run()
    rig.clock.advance(600)
    # 잡 스크립트가 결과를 올렸지만 서버에는 아직 running 이다(스크립트가 끝나기 직전)
    rig.job(12, state="running", reason=None)
    rig.gh.add_status(S1, Status(state, text, at=rig.clock.now))
    rig.run()
    rig.clock.advance(600)
    rig.job(
        12, state="succeeded" if state == "success" else "failed",
        exit_code=0 if state == "success" else 1, failed_step=None, last_step="secrets",
        summary="", cancelled_by=None, superseded_by=None, finished_at=iso(rig.clock.now - 500),
    )  # fmt: skip
    rig.run()
    assert [p["description"].split(" · ")[0] for p in rig.posts(S1)] == ["queued"]


def test_r15_the_watcher_still_covers_a_job_that_could_not_post_its_end(rig):
    # 잡이 supersede 로 취소되면 rcm-ci.sh 는 결과를 못 올린다 — 감시가 120초 뒤 덮는다
    rig.add_pr(7, S1, "feat/x", toml=repo_ci_toml())
    rig.exe.set(run={"stdout": submitted(12, S1)})
    rig.job(12, state="running", reason=None)
    rig.run()
    rig.gh.add_status(S1, Status("pending", "rcm job 12 running", at=rig.clock.now))
    rig.clock.advance(600)
    rig.job(
        12, state="cancelled", exit_code=None, failed_step=None, last_step="pytest",
        summary="", cancelled_by="superseded", superseded_by=13,
        finished_at=iso(rig.clock.now - 121),
    )  # fmt: skip
    rig.run()
    last = rig.posts(S1)[-1]
    assert (last["state"], last["description"]) == ("error", "superseded · #12 · by #13")


@pytest.mark.parametrize(("reason", "limit"), [("worker_down", 120), ("paused", 300)])
def test_r15_without_a_fallback_a_stuck_job_is_left_to_wait(rig, reason, limit):
    # 대신 돌 GitHub 이 없으니 취소하고 error 를 올리면 남는 것이 없다 — 배포 때 큐를 멈춘다
    rig.add_pr(7, S1, "feat/x", toml=repo_ci_toml())
    rig.exe.set(run={"stdout": submitted(12, S1)})
    rig.job(12, state="queued", reason=reason)
    rig.run()
    for _ in range(3):
        rig.clock.advance(limit)
        rig.run()
    assert [p["description"].split(" · ")[0] for p in rig.posts(S1)] == ["queued"]
    assert rig.exe.argvs("cancel") == []
    assert rig.log().count(f"{reason} but no fallback") == 1


def test_r15_the_github_token_can_come_from_a_command(rig, tmp_path):
    gh = fake_gh(tmp_path / "ghbin")
    rig.gh_token.unlink()
    rig.write_config(
        (),
        github_token_line=(
            f'github_token_command = ["{gh}", "auth", "token", "--user", "monocsp"]\n'
        ),
    )
    rig.add_pr(7, S1, "feat/x", toml=repo_ci_toml())
    rig.exe.set(run={"stdout": submitted(12, S1)})
    assert rig.run() == 0
    assert len(rig.exe.argvs("run")) == 1
    assert [p["description"].split(" · ")[0] for p in rig.posts(S1)] == ["queued"]
    assert (tmp_path / "ghbin" / "gh.argv").read_text().splitlines()[0] == (
        "auth token --user monocsp"
    )
    assert rig.run("--check") == 0, rig.printed
    assert not [ln for ln in rig.printed.splitlines() if ln.startswith("FAIL")]


def test_r15_a_failing_token_command_skips_the_repository_without_posting(rig, tmp_path):
    gh = fake_gh(tmp_path / "ghbin", rc=1)
    rig.write_config((), github_token_line=f'github_token_command = ["{gh}", "auth", "token"]\n')
    rig.add_pr(7, S1, "feat/x", toml=repo_ci_toml())
    rig.exe.set(run={"stdout": submitted(12, S1)})
    assert rig.run() == 0
    assert rig.exe.argvs("run") == [] and rig.gh.posted() == []
    assert "github_token_command" in rig.log() and "rc=1" in rig.log()
    assert rig.run("--check") == 1


def write_client_toml(path: Path, mode: int = 0o600) -> Path:
    path.write_text(
        '# rcm client\nserver = "http://100.64.0.1:8787"\n'
        f'token = "{RCM_TOKEN}"\nlabel = "owner@macmini"\n'
    )
    path.chmod(mode)
    return path


def test_r15_the_rcm_token_can_come_from_the_client_config(rig, tmp_path):
    client = write_client_toml(tmp_path / "client.toml")
    rig.rcm_token.unlink()
    rig.write_config((), rcm_token_line=f'rcm_client_config = "{client}"\n')
    rig.add_pr(7, S1, "feat/x", toml=repo_ci_toml())
    rig.exe.set(run={"stdout": submitted(12, S1)})
    rig.job(12, state="queued", reason=None)
    assert rig.run() == 0
    call = rig.exe.calls()[0]
    assert call["has_token"] and call["server"] == rig.rcm_http.url  # 서버는 감시 설정이 정한다
    seen = [r for r in rig.rcm_http.requests if r.path == "/jobs/12"]
    assert seen and seen[0].headers.get("authorization") == f"Bearer {RCM_TOKEN}"
    # 클라이언트 설정의 토큰은 보통 오너(admin) 것이다 — 경고하되 막지 않는다
    rig.rcm_http.route("GET", "/api/whoami", {"name": "owner", "admin": True})
    assert rig.run("--check") == 0, rig.printed
    warns = [ln for ln in rig.printed.splitlines() if ln.startswith("WARN")]
    assert any("admin true" in ln for ln in warns), rig.printed


def test_r15_an_open_client_config_stops_the_run(rig, tmp_path):
    client = write_client_toml(tmp_path / "client.toml", mode=0o644)
    rig.write_config((), rcm_token_line=f'rcm_client_config = "{client}"\n')
    rig.add_pr(7, S1, "feat/x", toml=repo_ci_toml())
    rig.exe.set(run={"stdout": submitted(12, S1)})
    assert watchmod.main(["--config", str(rig.config), "--once"], now_fn=rig.clock) == 2
    rig.capsys.readouterr()
    assert "0644" in rig.log()
    assert rig.exe.argvs("run") == [] and rig.gh.posted() == []


@pytest.mark.parametrize("which", ["rcm-both", "rcm-none", "github-both", "github-none"])
def test_r15_each_token_has_exactly_one_source(rig, tmp_path, which):
    client = write_client_toml(tmp_path / "client.toml")
    both_rcm = f'rcm_token_file = "{rig.rcm_token}"\nrcm_client_config = "{client}"\n'
    both_gh = f'github_token_file = "{rig.gh_token}"\ngithub_token_command = ["gh"]\n'
    lines = {
        "rcm-both": {"rcm_token_line": both_rcm},
        "rcm-none": {"rcm_token_line": ""},
        "github-both": {"github_token_line": both_gh},
        "github-none": {"github_token_line": ""},
    }[which]
    rig.write_config((), **lines)
    assert watchmod.main(["--config", str(rig.config), "--once"], now_fn=rig.clock) == 2
    err = rig.capsys.readouterr().err
    assert "cannot read" in err and RCM_TOKEN not in err and GH_TOKEN not in err


def test_r15_dry_run_prints_the_submission_and_writes_nothing(rig):
    rig.set_branch("dev", S2, toml=repo_ci_toml())
    rig.write_config(("dev",))
    rig.add_pr(7, S1, "feat/x", toml=repo_ci_toml())
    rig.exe.set(run={"stdout": submitted(12, S1)})
    assert rig.run("--dry-run") == 0
    assert "would run: rcm run rcm-ci --source git_ref --ref feat/x" in rig.printed
    assert "would run: rcm run rcm-ci --source git_ref --ref dev" in rig.printed
    assert rig.exe.argvs("run") == [] and rig.gh.posted() == []
    assert not (rig.state_dir / "monocsp__remote_ci_monitor.json").exists()


# ── 격리 검증(#245)이 남긴 후속 (docs/m5r-workplan.md §13.8) ──────────────────
#
# 1. 서버 재시작 순간의 제출 실패(`server_unreachable` · 시간 초과)는 세 바퀴까지 다시 낸다.
# 2. `--check` 는 감시의 `rcm` 이 서비스 venv 를 가리키면 WARN 이다.
# 3. 닫히거나 머지된 PR 의 queued 잡은 감시가 취소한다. 합류한 잡 · dev 에 있는 커밋은 둔다.

UNREACHABLE = {"rc": 2, "stderr": "rcm: cannot reach http://127.0.0.1:9 (ConnectionRefusedError)"}


def test_h6_an_unreachable_server_is_retried_for_three_rounds_then_error(rig, monkeypatch, capsys):
    """서버가 재시작하는 순간 온 push 가 다음 push 까지 머지를 막았다. 진짜 CLI 의 문장으로 낸다."""
    rc, stderr = real_cli_failure("server", monkeypatch, capsys)
    assert rc == 2 and watchmod.submit_reason(rc, stderr) == "server_unreachable"
    rig.add_pr(7, S1, "feat/x")
    rig.exe.set(run={"rc": rc, "stderr": stderr})
    for _ in range(5):
        rig.run()
        rig.clock.advance(60)
    assert len(rig.exe.argvs("run")) == 4  # 처음 한 번과 다시 낸 세 번
    assert [(p["state"], p["description"]) for p in rig.posts(S1)] == [
        ("pending", "queued · retrying submit (1/3)"),
        ("pending", "queued · retrying submit (2/3)"),
        ("pending", "queued · retrying submit (3/3)"),
        ("error", "unavailable · server_unreachable"),
    ]


def test_a_submit_that_works_on_retry_is_followed_like_a_first_one(rig):
    rig.add_pr(7, S1, "feat/x")
    rig.exe.set(run=[UNREACHABLE, {"stdout": submitted(12, S1)}])
    rig.job(12, state="queued", reason=None)
    rig.run()
    rig.clock.advance(60)
    rig.run()
    rig.job(12, state="running", reason=None)
    rig.clock.advance(60)
    rig.run()
    assert len(rig.exe.argvs("run")) == 2
    descriptions = [p["description"] for p in rig.posts(S1)]
    assert descriptions[0] == "queued · retrying submit (1/3)"
    assert descriptions[1].startswith("queued · #12")
    assert descriptions[2] == "running · #12"


def test_a_retry_stops_when_someone_else_posted_a_status(rig):
    rig.add_pr(7, S1, "feat/x")
    rig.exe.set(run=UNREACHABLE)
    rig.run()
    rig.clock.advance(30)
    # 그사이 세션이 손으로 냈고 잡 스크립트가 진행을 올렸다
    rig.gh.add_status(S1, Status("pending", "rcm job 30 running", at=rig.clock.now))
    rig.clock.advance(30)
    rig.run()
    assert len(rig.exe.argvs("run")) == 1
    assert len(rig.posts(S1)) == 1


def test_one_round_counts_one_retry_when_a_sha_is_both_a_pr_and_dev(rig):
    """dev → main PR 의 head 는 dev 의 head 와 같은 sha 다. 한 바퀴에 그 sha 를 두 번 만난다."""
    rig.write_config(("dev",))
    rig.set_branch("dev", S1)
    rig.add_pr(9, S1, "dev", base="main")
    rig.exe.set(run=UNREACHABLE)
    rig.run()
    assert len(rig.exe.argvs("run")) == 1
    assert [p["description"] for p in rig.posts(S1)] == ["queued · retrying submit (1/3)"]


def test_check_warns_when_the_watcher_calls_the_service_venv(rig, tmp_path, monkeypatch):
    """서비스 venv 의 rcm 은 배포 때 checkout 이 바뀌는 사이 실패할 수 있다. 링크도 따라간다."""
    venv = tmp_path / "rcm-venv"
    (venv / "bin").mkdir(parents=True)
    real = venv / "bin" / "rcm"
    real.write_bytes(rig.exe.path.read_bytes())
    real.chmod(0o755)
    link = tmp_path / "local-bin" / "rcm"
    link.parent.mkdir()
    link.symlink_to(real)
    monkeypatch.setattr(watchmod, "SERVICE_VENV", str(venv))
    assert rig.run("--check") == 0, rig.printed
    client = [ln for ln in rig.printed.splitlines() if "rcm client" in ln]
    assert client and client[0].startswith("OK"), rig.printed
    rig.printed = ""
    rig.config.write_text(rig.config.read_text().replace(str(rig.exe.path), str(link)))
    assert rig.run("--check") == 0, rig.printed  # 경고일 뿐 실패는 아니다
    client = [ln for ln in rig.printed.splitlines() if "rcm client" in ln]
    assert client and client[0].startswith("WARN"), rig.printed
    assert "service venv" in client[0] and "client venv" in client[0], client[0]


def ours(**doc) -> dict:
    """감시가 낸 잡의 조회 결과 — 요청자 이름표가 `ci-watch@<host>` 이고 합류자는 없다."""
    return {"requester": {"name": "owner", "label": f"ci-watch@{HOST}"}, "joiners": [], **doc}


def close_pr_after_queued(rig, **doc) -> None:
    rig.add_pr(7, S1, "feat/x")
    rig.exe.set(run={"stdout": submitted(12, S1)})
    rig.job(12, **ours(state="queued", reason=None))
    rig.run()
    if doc:
        rig.job(12, **ours(**doc))
    rig.gh.pulls.clear()  # 닫혔거나 머지됐다
    rig.clock.advance(60)
    rig.run()


def test_a_closed_pr_cancels_its_queued_job_once_and_posts_nothing(rig):
    close_pr_after_queued(rig)
    assert rig.exe.argvs("cancel") == [["cancel", "12"]]
    assert "pr_gone" in rig.log()
    rig.job(12, **ours(state="cancelled", cancelled_by="owner", finished_at=iso(rig.clock.now)))
    rig.clock.advance(600)
    rig.run()
    assert len(rig.exe.argvs("cancel")) == 1
    assert [p["description"].split(" · ")[0] for p in rig.posts(S1)] == ["queued"]


def test_a_closed_pr_leaves_a_running_job_alone(rig):
    close_pr_after_queued(rig, state="running", reason=None)
    assert rig.exe.argvs("cancel") == []


@pytest.mark.parametrize(
    "doc",
    [
        {"joiners": [{"name": "session", "label": "me@mac", "joined_at": iso(T0)}]},
        {"requester": {"name": "owner", "label": "me@mac"}},
        {"requester": None},
    ],
    ids=["someone_joined", "someone_else_asked", "unknown_requester"],
)
def test_a_closed_pr_keeps_a_job_it_does_not_own_alone(rig, doc):
    """감시가 낸 잡에 남이 합류했거나 감시가 남의 잡에 합류했다. 취소하면 남의 검사가 죽는다."""
    close_pr_after_queued(rig, **{**ours(state="queued", reason=None), **doc})
    assert rig.exe.argvs("cancel") == []


def test_a_closed_pr_whose_commit_is_the_dev_head_is_not_cancelled(rig):
    """dev 에 그대로 들어간 커밋이면 dev push 의 제출이 같은 잡에 합류했을 수 있다."""
    rig.write_config(("dev",))
    rig.set_branch("dev", S2)
    rig.exe.set(run=[{"stdout": submitted(12, S1)}, {"stdout": submitted(13, S2)}])
    rig.add_pr(7, S1, "feat/x")
    rig.job(12, **ours(state="queued", reason=None))
    rig.run()
    rig.gh.pulls.clear()
    rig.set_branch("dev", S1)  # fast-forward 로 dev 가 그 커밋이 됐다
    rig.clock.advance(60)
    rig.run()
    assert rig.exe.argvs("cancel") == []


def test_an_unreadable_pull_list_cancels_nothing(rig):
    rig.add_pr(7, S1, "feat/x")
    rig.exe.set(run={"stdout": submitted(12, S1)})
    rig.job(12, **ours(state="queued", reason=None))
    rig.run()
    rig.gh.fail[f"/repos/{REPO}/pulls"] = [500]
    rig.clock.advance(60)
    rig.run()
    assert rig.exe.argvs("cancel") == []


# ── #246 격리 검증이 찾은 것 ──────────────────────────────────────────────────


def test_a_submission_the_server_moved_to_a_newer_commit_is_not_cancelled_as_gone(rig):
    """서버가 `--ref` 를 더 새 커밋 S2 로 풀었다. S2 는 아직 열린 PR 목록에 없지만 닫힌 것이 아니다.
    취소하면 S2 는 이미 본 sha 라 다시 내지 않고 머지가 막힌다."""
    rig.add_pr(7, S1, "feat/x")
    rig.exe.set(run={"stdout": submitted(12, S2)})
    rig.job(12, **ours(state="queued", reason=None))
    rig.run()
    assert rig.exe.argvs("cancel") == []
    rig.clock.advance(60)
    rig.run()  # PR 목록이 아직 S1 을 말한다
    assert rig.exe.argvs("cancel") == []
    rig.gh.pulls[0]["head"]["sha"] = S2
    rig.gh.files[(S2, ".rcm/ci.toml")] = RCM_REPO_CI_TOML
    rig.clock.advance(60)
    rig.run()
    assert rig.exe.argvs("cancel") == [] and len(rig.exe.argvs("run")) == 1


@pytest.mark.parametrize("hook", [False, True], ids=["no_hook", "hook_posted_cancelled"])
def test_a_reopened_pr_whose_job_was_cancelled_is_submitted_again(rig, hook):
    """알림 훅이 있으면 취소 몇 초 뒤 그 잡의 끝(`unavailable · #12 · cancelled`)을 올린다. 취소한
    그 잡의 결과이니 감시가 남긴 것과 같다 — 다시 연 PR 을 막지 않는다(#246 재검증)."""
    close_pr_after_queued(rig)
    assert rig.exe.argvs("cancel") == [["cancel", "12"]]
    rig.job(12, **ours(state="cancelled", cancelled_by="owner", finished_at=iso(rig.clock.now)))
    if hook:
        end = Status("error", "unavailable · #12 · cancelled", at=rig.clock.now + 2)
        rig.gh.add_status(S1, end)
    rig.exe.set(run={"stdout": submitted(13, S1)})
    rig.add_pr(7, S1, "feat/x")  # 다시 열었다
    rig.clock.advance(60)
    rig.run()
    assert len(rig.exe.argvs("run")) == 2
    assert rig.posts(S1)[-1]["description"].startswith("queued · #13")


def test_a_reopened_pr_that_someone_already_ran_is_not_submitted_again(rig):
    close_pr_after_queued(rig)
    rig.gh.add_status(S1, Status("success", "rcm job 30 passed", at=rig.clock.now + 1))
    rig.add_pr(7, S1, "feat/x")
    rig.clock.advance(60)
    rig.run()
    assert len(rig.exe.argvs("run")) == 1


def test_every_page_of_open_pull_requests_is_read(rig, monkeypatch):
    monkeypatch.setattr(watchmod, "PULLS_PER_PAGE", 2)
    for n, sha in enumerate((S1, S2, S3)):
        rig.add_pr(7 + n, sha, f"feat/{n}")
    rig.exe.set(run=[{"stdout": submitted(12 + n, sha)} for n, sha in enumerate((S1, S2, S3))])
    rig.run()
    assert [a[a.index("--ref") + 1] for a in rig.exe.argvs("run")] == ["feat/0", "feat/1", "feat/2"]


def test_an_unread_later_page_cancels_nothing(rig, monkeypatch):
    monkeypatch.setattr(watchmod, "PULLS_PER_PAGE", 2)
    for n, sha in enumerate((S1, S2, S3)):
        rig.add_pr(7 + n, sha, f"feat/{n}")
        rig.job(12 + n, **ours(state="queued", reason=None))
    rig.exe.set(run=[{"stdout": submitted(12 + n, sha)} for n, sha in enumerate((S1, S2, S3))])
    rig.run()
    assert len(rig.exe.argvs("run")) == 3
    rig.gh.fail[f"/repos/{REPO}/pulls?state=open&per_page=2&page=2"] = [500]
    rig.clock.advance(60)
    rig.run()
    assert rig.exe.argvs("cancel") == []  # S3 는 둘째 쪽에 있다 — 못 읽었으니 닫힌 것이 아니다


def test_a_missing_rcm_executable_is_named_rcm_missing(rig):
    rig.config.write_text(
        rig.config.read_text().replace(str(rig.exe.path), str(rig.tmp / "gone" / "rcm"))
    )
    rig.add_pr(7, S1, "feat/x")
    for _ in range(4):
        rig.run()
        rig.clock.advance(60)
    assert "could not start" in rig.log()
    posts = [(p["state"], p["description"]) for p in rig.posts(S1)]
    assert posts[0] == ("pending", "queued · retrying submit (1/3)")
    assert posts[-1] == ("error", "unavailable · rcm_missing"), posts
