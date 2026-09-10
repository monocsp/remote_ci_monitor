"""CLI(M5i) — 없는 잡의 `rcm wait` 끝줄 · `rcm check` 의 `local data dir` 행 · `--ref` 제출 줄.

명세는 `docs/gate-replay-fixes-workplan.md` §3 I3 · I4. **구현보다 먼저 썼다(test-first) — 빨간
것이 정상이다.**

- I3 은 **진짜 대기 루프**(`client.wait_for_job` → `_poll_for_job`)에 가짜 `Client` 를 넣고
  `cli._wait` 이 그 뒤에 무엇을 찍는지 잰다. `wait_for_job` 을 대본으로 바꾸면 「루프가 사유를
  구조화해 돌려준다」는 절반을 안 재는 셈이라 여기서는 안 바꾼다(잠긴 키 집합이 숨긴 버그를
  보라 — 실제 생산자를 실제 소비자에 넣는다).
- 확정 404 만 `log: rcm logs N` 을 뺀다. 연결 실패는 여전히 exit 3 + 로그 길이다(결정 70).
- I4 는 `rcm check` 의 행 이름이다 — 서버는 `data_dir` 을 API 로 내리지 않으므로 그 행은 **로컬
  설정**의 사실이다. 이름이 그렇게 말해야 `--server` 가 가리키는 원격 서버의 경로로 읽히지 않는다.
- I2 의 제출 줄(검증 V6.2 에서 발견): `rcm run gate --ref <40-hex>` 의 `submitted job #1 (gate ·
  <ref> @<sha>)` 가 목록 칸과 같은 규칙을 따른다 — ref 가 곧 sha 면 한 번만. 진짜 서버에 진짜
  bare 레포로 제출해 stderr 를 읽는다(`--no-wait` 경로와 기다리는 경로 둘 다).
"""

from __future__ import annotations

import json
import shutil

import pytest

from remote_ci_monitor import cli
from remote_ci_monitor import client as client_module
from remote_ci_monitor.client import ClientError, wait_for_job
from remote_ci_monitor.core.render_text import CAUSE_NOT_FOUND, CAUSE_UNREACHABLE
from test_cli_m1 import run
from test_server import Server
from test_server_m3 import build_bare_repo, git_server

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")


class Missing:
    """`GET /jobs/999` 가 404 인 서버."""

    server = "http://macmini:8787"

    def job(self, job_id: int) -> dict:
        raise ClientError(404, f"job {job_id} not found", {"hint": "try: rcm jobs"})


class Unreachable:
    """닿지 않는 서버 — 무엇이 있는지 모른다."""

    server = "http://127.0.0.1:1"

    def job(self, job_id: int) -> dict:
        raise ClientError(0, "connection refused")


def last_json(out: str) -> dict:
    lines = [ln for ln in out.strip().splitlines() if ln.startswith("{")]
    assert lines, f"no JSON line in stdout: {out!r}"
    return json.loads(lines[-1])


# ── I3. `rcm wait` 의 끝줄 ──────────────────────────────────────────────────


def test_the_wait_loop_returns_a_structured_cause_that_still_reads_as_text():
    """사유는 사람용 문구이면서 `cause` 를 든다 — 옛 호출자(`"not found" in reason`)도 그대로."""
    code, job, reason = wait_for_job(Missing(), 999, poll_seconds=0, use_sse=False)
    assert code == 3 and job is None
    assert "not found" in reason
    assert reason.cause == CAUSE_NOT_FOUND


def test_a_missing_job_gets_no_log_hint(capsys):
    """`rcm wait --job 999` 에 없는 잡: `log: rcm logs 999` 는 아무것도 안 가리킨다."""
    rc = cli._wait(Missing(), 999, timeout=None, joined=False, use_sse=False)
    cap = capsys.readouterr()
    assert rc == 3
    assert "not found" in cap.err
    assert "rcm logs 999" not in cap.err, cap.err
    doc = last_json(cap.out)
    assert doc["job_id"] == 999 and doc["wait_exit_code"] == 3 and doc["state"] is None


def test_an_unreachable_server_keeps_the_log_hint(capsys, monkeypatch):
    """연결 실패는 「모른다」다 — 잡은 있을 수 있고 로그는 그때 가장 필요하다(결정 70)."""
    monkeypatch.setattr(client_module, "CONNECTION_GRACE_SECONDS", -1)  # 첫 실패에서 바로 포기
    code, job, reason = wait_for_job(Unreachable(), 999, poll_seconds=0, use_sse=False)
    assert code == 3 and job is None and reason.cause == CAUSE_UNREACHABLE
    rc = cli._wait(Unreachable(), 999, timeout=None, joined=False, use_sse=False)
    cap = capsys.readouterr()
    assert rc == 3
    assert "lost contact" in cap.err
    assert "log: rcm logs 999" in cap.err, cap.err


@pytest.mark.parametrize("use_sse", [True, False])
def test_both_wait_paths_agree_on_the_404_cause(use_sse):
    """SSE 경로와 폴링 경로가 같은 사유를 돌려준다 — 한쪽만 고치면 `--poll` 이 다른 답을 낸다."""
    _, _, reason = wait_for_job(Missing(), 999, poll_seconds=0, use_sse=use_sse)
    assert reason.cause == CAUSE_NOT_FOUND


# ── I2. `rcm run --ref <sha>` 의 제출 줄 ───────────────────────────────────


@pytest.fixture
def env(monkeypatch, tmp_path):
    """test_cli_m1.env 와 같다 — `use(srv)` 가 서버·토큰을 환경변수로 건다."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("RCM_LABEL", raising=False)

    def use(server: Server, token: str = "alice") -> None:
        monkeypatch.setenv("RCM_SERVER", f"http://127.0.0.1:{server.port}")
        monkeypatch.setenv("RCM_TOKEN", server.tokens[token])

    return use


@pytest.fixture
def bare(tmp_path):
    return build_bare_repo(tmp_path)


@pytest.fixture
def git_srv(tmp_path, bare):
    s = git_server(tmp_path, bare)
    yield s
    s.close()


@pytest.fixture
def git_live(tmp_path, bare):
    s = git_server(tmp_path, bare, workers=True)
    yield s
    s.close()


@pytest.fixture
def cwd(monkeypatch, tmp_path):
    """스냅샷을 찍으면 곧바로 티가 나게 빈 디렉터리에서 돌린다."""
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    return empty


def submitted_line(err: str) -> str:
    return next(ln for ln in err.splitlines() if ln.startswith("submitted job"))


@needs_git
def test_the_no_wait_submit_line_shows_a_full_sha_ref_once(git_srv, bare, env, cwd, capsys):
    env(git_srv)
    code, _, err = run(capsys, ["run", "deploy", "--ref", bare.main_sha, "--no-wait"])
    assert code == 0, err
    line = submitted_line(err)
    assert bare.main_sha not in line, line
    assert line.count(bare.main_sha[:7]) == 1, line
    assert f"(deploy · @{bare.main_sha[:7]})" in line, line


@needs_git
def test_the_no_wait_submit_line_keeps_a_branch_ref(git_srv, bare, env, cwd, capsys):
    env(git_srv)
    code, _, err = run(capsys, ["run", "deploy", "--ref", "main", "--no-wait"])
    assert code == 0, err
    assert f"(deploy · main @{bare.main_sha[:7]})" in submitted_line(err), err


@needs_git
def test_the_waiting_submit_line_shows_a_full_sha_ref_once(git_live, bare, env, cwd, capsys):
    """기다리는 경로는 다른 문자열 리터럴이다 — 한쪽만 고치면 이 줄이 다시 두 번 찍는다."""
    env(git_live)
    code, _, err = run(capsys, ["run", "deploy", "--ref", bare.main_sha])
    assert code == 0, err
    line = submitted_line(err)
    assert bare.main_sha not in line, line
    assert f"(deploy · @{bare.main_sha[:7]})" in line, line
