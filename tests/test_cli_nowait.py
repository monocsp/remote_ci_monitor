"""CLI — `rcm run --no-wait` 의 응답: 진짜 상태 · 순번 · 대기 · ETA.

제출만 하고 빠지는 세션도 「내가 몇 번째냐 · 언제 끝나냐」를 알아야 한다. 기다리는 경로는 이미
`describe()` 로 그 값을 한 줄로 보여 주고 `rcm eta` 도 보여 준다 — `--no-wait` 만 빠져 있었다.
`state: "submitted"` 는 실제 상태 이름도 아니었다(진짜는 `uploading`·`queued`·…).

표시용 조회는 제출을 깨뜨리지 않는다: 실패하면 순번 조각만 빠지고 종료 코드는 그대로 0 이다 —
`--no-wait` 의 0 은 「제출됐다」는 뜻이지 「조회됐다」가 아니다. 구현보다 먼저 썼다(test-first).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from remote_ci_monitor.client import Client, ClientError
from test_cli_m1 import last_json, run
from test_server import Server

# ── 도우미 ───────────────────────────────────────────────────────────────────


@pytest.fixture
def env(monkeypatch, tmp_path):
    """test_cli_m5.env 와 같다 — `use(srv, token)` 이 서버·토큰을 환경변수로 건다."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("XDG_CONFIG_HOME", "RCM_LABEL", "RCM_CONFIG"):
        monkeypatch.delenv(var, raising=False)

    def use(server: Server, token: str | None = "alice") -> None:
        monkeypatch.setenv("RCM_SERVER", f"http://127.0.0.1:{server.port}")
        if token is None:
            monkeypatch.delenv("RCM_TOKEN", raising=False)
        else:
            monkeypatch.setenv("RCM_TOKEN", server.tokens[token])

    return use


@pytest.fixture
def srv(tmp_path):
    """워커가 없다 — 낸 잡은 전부 `queued` 로 줄을 선다(순번이 시간에 안 흔들린다)."""
    s = Server(tmp_path, workers=False)
    yield s
    s.close()


@pytest.fixture
def live(tmp_path):
    s = Server(tmp_path, workers=True)
    yield s
    s.close()


@pytest.fixture
def tree(tmp_path) -> Path:
    """`rcm run --dir` 에 줄 작은 트리(파일 둘). 기본 Server 의 10 KB 상한 안."""
    root = tmp_path / "tree"
    root.mkdir()
    (root / "hello.txt").write_text("hello\n")
    (root / "scripts").mkdir()
    (root / "scripts" / "gate.sh").write_text("#!/bin/sh\necho gate\n")
    return root


def no_wait(capsys, tree: Path, preset: str, *extra: str) -> tuple[int, dict, str]:
    """`rcm run <preset> --no-wait --dir <tree> …` → (종료 코드, JSON, stderr)."""
    code, out, err = run(capsys, ["run", preset, "--no-wait", "--dir", str(tree), *extra])
    return code, last_json(out), err


def queue_rows(server: Server) -> list[dict]:
    return server.req("GET", "/api/status")[1]["pools"][0]["queue"]


# ── 순번·ETA 를 싣는다 ───────────────────────────────────────────────────────


def test_no_wait_json_carries_the_real_state_position_and_estimate(srv, env, tree, capsys):
    """제출만 하고 빠져도 「몇 번째냐 · 언제 끝나냐」가 응답에 있다.

    `rcm eta --job N` 을 따로 칠 일이 없다.
    """
    env(srv)
    _code, first, _err = no_wait(capsys, tree, "ok")
    code, body, err = no_wait(capsys, tree, "bad")
    assert code == 0, err
    assert body["state"] == "queued"  # 「submitted」 는 실제 상태 이름이 아니다
    assert body["position"] == 2
    assert body["reason"] == "waiting_for_lane"
    assert body["ahead_job_id"] == first["job_id"]
    assert body["blocked_by"] is None
    est = body["estimate"]
    assert est["wait_seconds"] > 0 and est["finish_at"]
    assert est["expected_seconds"] > 0 and est["source"]


def test_no_wait_stderr_says_the_position_the_wait_and_the_eta(srv, env, tree, capsys):
    """사람이 읽는 줄도 같이 — `submitted job #2 queued · 2nd in line · wait 10m 00s · eta …`."""
    env(srv)
    no_wait(capsys, tree, "ok")
    _code, body, err = no_wait(capsys, tree, "bad")
    line = next(ln for ln in err.splitlines() if "submitted job" in ln)
    assert f"#{body['job_id']}" in line and "queued" in line
    assert "2nd in line" in line
    assert "wait 10m 00s" in line  # 표본이 없는 잡의 기본 600초
    assert "eta " in line
    assert body["url"] in line  # 링크는 여전히 그 줄 끝에 있다


def test_a_joined_no_wait_reports_that_jobs_own_position(srv, env, tree, capsys):
    """합류한 세션도 남의 잡 번호만 받고 끝나지 않는다 — 그 잡의 실제 순번을 본다."""
    env(srv)
    _code, first, _err = no_wait(capsys, tree, "ok")
    no_wait(capsys, tree, "bad")  # 뒤에 하나 더 세워 순번이 1 이 우연이 아니게
    env(srv, "bob")  # 다른 세션이 같은 트리를 낸다
    code, body, err = no_wait(capsys, tree, "ok")
    assert code == 0, err
    assert body["joined"] is True and body["job_id"] == first["job_id"]
    assert body["state"] == "queued" and body["position"] == 1
    assert body["estimate"]["finish_at"]
    line = next(ln for ln in err.splitlines() if "joined job" in ln)
    assert "1st in line" in line and "eta " in line, line


def test_a_running_job_has_no_position_and_no_0th_in_line(live, env, tree, capsys):
    """`running` 은 `position` 이 null 이다 — 「0번째」 같은 걸 지어내지 않고 그 조각을 뺀다."""
    env(live)
    _code, first, _err = no_wait(capsys, tree, "slow")
    live.wait_state(first["job_id"], "running")
    env(live, "bob")
    code, body, err = no_wait(capsys, tree, "slow")
    assert code == 0, err
    assert body["joined"] is True and body["job_id"] == first["job_id"]
    assert body["state"] == "running" and body["position"] is None
    line = next(ln for ln in err.splitlines() if "joined job" in ln)
    assert "in line" not in line and "0th" not in line, line
    assert "running" in line and "elapsed" in line, line


# ── 표시용 조회가 제출을 깨뜨리지 않는다 ────────────────────────────────────


def test_a_broken_display_lookup_still_prints_the_submission_and_exits_0(
    srv, env, tree, capsys, monkeypatch
):
    """조회가 깨져도 잡은 이미 큐에 있다.

    종료 코드는 「제출됐다」는 뜻이지 「조회됐다」가 아니다.
    """
    env(srv)

    def boom(self, job_id, **kw):
        raise ClientError(0, f"cannot reach {self.server}: refused")

    monkeypatch.setattr(Client, "job", boom)
    code, body, err = no_wait(capsys, tree, "ok")
    assert code == 0, err
    assert body["joined"] is False and body["url"]
    assert body["state"] == "queued"  # 업로드 응답이 말해 준 진짜 상태
    for key in ("position", "reason", "ahead_job_id", "blocked_by", "estimate"):
        assert key not in body, key  # 모르는 값을 null 로도 지어내지 않는다
    assert "submitted job" in err
    assert [r["id"] for r in queue_rows(srv)] == [body["job_id"]]  # 잡은 진짜로 줄에 서 있다


def test_the_display_lookup_asks_once_with_a_short_timeout(srv, env, tree, capsys, monkeypatch):
    """제출 경로에 왕복을 더 다는 것이라 한 번만, 그리고 오래 붙들지 않는다."""
    env(srv)
    calls: list = []
    real = Client.job

    def counting(self, job_id, **kw):
        calls.append((job_id, kw.get("timeout")))
        return real(self, job_id, **kw)

    monkeypatch.setattr(Client, "job", counting)
    code, body, err = no_wait(capsys, tree, "ok")
    assert code == 0, err
    assert calls == [(body["job_id"], pytest.approx(5.0))], calls
