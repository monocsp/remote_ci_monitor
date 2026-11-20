"""M5r H: 빌드 머신의 감시 스크립트 `rcm_ci_watch.py` — 열린 PR 과 push 브랜치를 보고 잡을 낸다.

docs/m5r-workplan.md §2.6 · §5.4 · §6 H. GitHub 은 `FakeGitHub`, `rcm` 클라이언트는 argv 를 적는
가짜 실행 파일, 잡 조회(`GET /jobs/{id}`)는 `FakeRcm` 이다. 시계는 주입한다. 시간 제한 시험만
모듈 상수를 줄여 진짜 시간으로 잰다(그래도 몇 초 안에 끝난다).
"""

from __future__ import annotations

import json
import os
import socket
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
    ) -> None:
        self.config.write_text(
            f'rcm = "{self.exe.path}"\n'
            f'server = "{self.rcm_http.url}"\n'
            f'rcm_token_file = "{self.rcm_token}"\n'
            f'state_dir = "{self.state_dir}"\n'
            f'log_file = "{self.log_file}"\n'
            f"job_url = {'true' if job_url else 'false'}\n"
            f'github_api = "{self.gh.url}"\n\n' + repos_before + "[[repos]]\n"
            f'github = "{REPO}"\n'
            f'github_token_file = "{self.gh_token}"\n'
            f"branches = {json.dumps(list(branches))}\n"
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
    [("server", "server_unreachable"), ("preset", "unknown_preset"), ("token", "rcm_auth")],
    ids=["h6_server", "h7_preset", "h23_rcm_token"],
)
def test_h6_h7_h23_a_failed_submit_posts_unavailable_with_a_code(
    rig, monkeypatch, capsys, case, code
):
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
    rig.run()
    assert rig.posts(S1) == []
    assert [p["description"].split(" · ")[0] for p in rig.posts(S2)] == ["queued"]


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


def test_h13_two_processes_at_once_only_one_runs(rig, monkeypatch):
    # 진짜 동시성 — 앞 프로세스가 GitHub 요청 하나(0.3초)에 머무는 동안 뒤 프로세스가 뜬다
    monkeypatch.setattr(cfgmod, "HTTP_TIMEOUT", 0.3)
    rig.add_pr(7, S1, "feat/x")
    rig.exe.set(run={"stdout": submitted(12, S1)})
    rig.gh.hang.add(f"/repos/{REPO}/actions/")  # 첫 프로세스가 잠금을 쥔 채 머문다
    first = threading.Thread(
        target=watchmod.main, args=(["--config", str(rig.config), "--once"],),
        kwargs={"now_fn": rig.clock},
    )  # fmt: skip
    first.start()
    deadline = time.monotonic() + 3
    while not lock_path(rig).exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    started = time.monotonic()
    assert watchmod.main(["--config", str(rig.config), "--once"], now_fn=rig.clock) == 0
    assert time.monotonic() - started < 0.5
    first.join(10)
    assert len(rig.exe.argvs("run")) == 1


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
    assert rig.gh.posted() == []
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
