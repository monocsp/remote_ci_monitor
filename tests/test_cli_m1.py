"""CLI(M1) — `rcm presets` · `top` · `jobs` · `eta` · `logs [--follow]` · `wait`(SSE 우선 → 폴링
폴백 · `--poll`). 명세는 docs/m1-workplan.md 0(F·G) · 5절.

`main(argv)` 를 in-process 로 부르고 `RCM_SERVER`/`RCM_TOKEN` 만으로 서버를 가리킨다.
HOME 을 임시 디렉터리로 옮겨 사용자의 client.toml 이 섞이지 않게 한다.
"""

import json
import re
import threading
import time
from datetime import UTC, datetime

import pytest

from remote_ci_monitor.cli import main
from remote_ci_monitor.client import Client
from test_server import Server
from test_server_m1 import StubSampler, host_sample


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path, workers=False)
    yield s
    s.close()


@pytest.fixture
def live(tmp_path):
    s = Server(tmp_path, workers=True)
    yield s
    s.close()


@pytest.fixture
def env(monkeypatch, tmp_path):
    """`use(srv, token)` — 그 서버·토큰을 환경변수로 건다. token=None 이면 토큰 없음."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("RCM_LABEL", raising=False)

    def use(server: Server, token: str | None = "alice") -> None:
        monkeypatch.setenv("RCM_SERVER", f"http://127.0.0.1:{server.port}")
        if token is None:
            monkeypatch.delenv("RCM_TOKEN", raising=False)
        else:
            monkeypatch.setenv("RCM_TOKEN", server.tokens[token])

    return use


def run(capsys, argv: list[str]) -> tuple[int, str, str]:
    code = main(argv)
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def last_json(out: str) -> dict:
    lines = [ln for ln in out.strip().splitlines() if ln.startswith("{")]
    assert lines, f"no JSON line in stdout: {out!r}"
    return json.loads(lines[-1])


def count_events(monkeypatch) -> list:
    """`Client.events` 호출을 세되 원래 동작은 그대로 둔다."""
    calls: list = []
    original = Client.events

    def counting(self, *args, **kwargs):
        calls.append((args, kwargs))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Client, "events", counting)
    return calls


def run_ok_job(server: Server, preset: str = "ok", token: str = "alice", tree_hash=None) -> int:
    kw = {"tree_hash": tree_hash} if tree_hash else {}
    jid = server.submit(token=token, preset=preset, **kw)[1]["job_id"]
    assert server.upload(jid, token=token)[0] == 200
    return jid


# ── presets · top ────────────────────────────────────────────────────────────


def test_presets_lists_names_and_input_choices(srv, env, capsys):
    env(srv)
    code, out, _ = run(capsys, ["presets"])
    assert code == 0
    for name in ("ok", "bad", "slow", "gate"):
        assert name in out
    assert "scope" in out and "full" in out and "fast" in out  # 입력 스키마의 choices


def test_top_json_is_status_schema_v1(srv, env, capsys):
    env(srv)
    code, out, _ = run(capsys, ["top", "--json"])
    assert code == 0
    doc = json.loads(out)
    assert doc["schema_version"] == 1 and len(doc["pools"]) == 1
    assert "sse_connections" in doc["server"]


def test_top_prints_render_text_header_and_host(srv, env, capsys):
    env(srv)
    code, out, _ = run(capsys, ["top"])
    assert code == 0
    assert out.lstrip().startswith("━━━ rcm ·"), out
    assert "queue — empty" in out and "host — no sample yet" in out
    srv.app.sampler = StubSampler([host_sample(datetime.now(UTC), age_seconds=2)])
    jid = srv.submit()[1]["job_id"]
    code, out, _ = run(capsys, ["top"])
    assert code == 0
    assert "━━━ rcm · macmini ·" in out and "host  macmini" in out
    assert f"#{jid}" in out and "uploading" in out


def test_top_without_token_still_works(srv, env, capsys):
    env(srv, token=None)  # 읽기는 토큰이 필요 없다
    code, out, _ = run(capsys, ["top", "--json"])
    assert code == 0 and json.loads(out)["schema_version"] == 1


# ── jobs ─────────────────────────────────────────────────────────────────────


def test_jobs_shows_submitted_job_and_state_filter(srv, env, capsys):
    env(srv)
    jid = srv.submit()[1]["job_id"]
    code, out, _ = run(capsys, ["jobs"])
    assert code == 0 and f"#{jid}" in out and "uploading" in out
    code, out, _ = run(capsys, ["jobs", "--state", "uploading"])
    assert code == 0 and f"#{jid}" in out
    code, out, _ = run(capsys, ["jobs", "--state", "running"])
    assert code == 0 and f"#{jid}" not in out
    code, out, _ = run(capsys, ["jobs", "--json"])
    assert code == 0
    json.loads(out)  # 한 문서
    assert re.search(rf'"id":\s*{jid}\b', out)


def test_jobs_mine_filters_by_requester_and_joiner(srv, env, capsys):
    jid = srv.submit()[1]["job_id"]  # alice 의 잡
    env(srv, "bob")
    code, out, _ = run(capsys, ["jobs", "--mine"])
    assert code == 0 and f"#{jid}" not in out
    code, out, _ = run(capsys, ["jobs"])
    assert code == 0 and f"#{jid}" in out  # 필터 없이는 보인다
    env(srv, "alice")
    code, out, _ = run(capsys, ["jobs", "--mine"])
    assert code == 0 and f"#{jid}" in out
    assert srv.submit(token="bob")[1]["joined"] is True  # bob 이 합류하면 bob 의 잡이기도 하다
    env(srv, "bob")
    code, out, _ = run(capsys, ["jobs", "--mine"])
    assert code == 0 and f"#{jid}" in out
    env(srv, token=None)
    code, _, err = run(capsys, ["jobs", "--mine"])
    assert code == 2 and "token" in err


def test_jobs_includes_recent_finished(live, env, capsys):
    env(live)
    jid = run_ok_job(live)
    live.wait_terminal(jid)
    code, out, _ = run(capsys, ["jobs"])
    assert code == 0 and f"#{jid}" in out and "succeeded" in out


# ── eta ──────────────────────────────────────────────────────────────────────


def test_eta_preset_prints_position_ahead_and_confidence(srv, env, capsys):
    env(srv)
    code, out, _ = run(capsys, ["eta", "gate", "-f", "scope=full"])
    assert code == 0
    assert "in line" in out or "ahead" in out, out
    assert "low" in out  # 서버가 실은 estimate.confidence
    srv.submit()
    srv.submit(token="bob", tree_hash="ab" * 32)
    code, out, _ = run(capsys, ["eta", "gate"])
    assert code == 0 and "3rd in line" in out and "ahead" in out, out
    code, out, _ = run(capsys, ["eta", "gate", "-f", "scope=full", "--json"])
    assert code == 0
    doc = json.loads(out)
    row = doc.get("job", doc)
    assert row["position"] == 3 and row["estimate"]["confidence"] == "low"


def test_eta_job_and_usage_errors(srv, env, capsys):
    env(srv)
    jid = srv.submit()[1]["job_id"]
    code, out, _ = run(capsys, ["eta", "--job", str(jid)])
    assert code == 0 and f"#{jid}" in out and "1st in line" in out, out
    code, _, err = run(capsys, ["eta", "nope"])
    assert code == 2 and "unknown preset" in err
    code, _, err = run(capsys, ["eta", "gate", "-f", "scope=huge"])
    assert code == 2 and "is not one of" in err
    code, _, err = run(capsys, ["eta", "gate", "-f", "no-equals"])
    assert code == 2


def test_eta_without_token_is_allowed(srv, env, capsys):
    env(srv, token=None)
    code, out, _ = run(capsys, ["eta", "gate", "-f", "scope=full"])
    assert code == 0 and ("in line" in out or "ahead" in out)


# ── logs ─────────────────────────────────────────────────────────────────────


def test_logs_prints_finished_log_and_enforces_ownership(live, env, capsys):
    jid = run_ok_job(live)
    live.wait_terminal(jid)
    env(live)
    code, out, _ = run(capsys, ["logs", str(jid)])
    assert code == 0
    assert "::rcm::step::a" in out and "hello" in out  # 로그 원문 그대로
    env(live, "bob")  # 남의 잡 → 403 → 2
    code, _, err = run(capsys, ["logs", str(jid)])
    assert code == 2 and err
    env(live, "admin")  # admin 은 된다
    assert run(capsys, ["logs", str(jid)])[0] == 0
    env(live, token=None)  # 로그는 토큰 필수
    assert run(capsys, ["logs", str(jid)])[0] == 2


def test_logs_follow_returns_after_job_ends(live, env, capsys):
    env(live)
    jid = run_ok_job(live, preset="slow")
    live.wait_state(jid, "running")
    log_path = live.app.log_path(jid)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and "line2" not in (
        log_path.read_text() if log_path.exists() else ""
    ):
        time.sleep(0.05)
    assert "line2" in log_path.read_text()

    def cancel_later() -> None:
        time.sleep(0.5)  # follow 가 최소 한 번 읽을 시간
        live.req("POST", f"/jobs/{jid}/cancel", token="alice", json_body={})

    canceller = threading.Thread(target=cancel_later)
    canceller.start()
    t0 = time.monotonic()
    code, out, _ = run(capsys, ["logs", str(jid), "--follow"])
    canceller.join(timeout=5)
    assert code == 0, out
    assert "line1\nline2\n" in out
    assert time.monotonic() - t0 < 10
    assert live.wait_terminal(jid).state == "cancelled"


# ── wait ─────────────────────────────────────────────────────────────────────


def test_wait_uses_sse_and_exits_with_job_code(live, env, capsys, monkeypatch):
    calls = count_events(monkeypatch)
    env(live)
    jid = run_ok_job(live)
    code, out, _ = run(capsys, ["wait", "--job", str(jid)])
    assert code == 0
    body = last_json(out)
    assert body["state"] == "succeeded" and body["wait_exit_code"] == 0
    assert calls and any(f"/jobs/{jid}/events" in repr(c) for c in calls), calls
    # 실패한 잡은 1. 이미 끝난 잡은 SSE 를 열든(hello + job_finished) 조회 한 번으로 끝내든
    # 종료 코드만 같으면 된다 — 경로는 단정하지 않는다.
    bad = run_ok_job(live, preset="bad", tree_hash="ef" * 32)
    live.wait_terminal(bad)
    code, out, _ = run(capsys, ["wait", "--job", str(bad)])
    assert code == 1 and last_json(out)["state"] == "failed"


def test_wait_falls_back_to_polling_when_sse_is_refused(tmp_path, env, capsys, monkeypatch):
    server = Server(tmp_path, workers=True, sse_max_connections=0)
    try:
        calls = count_events(monkeypatch)
        env(server)
        assert server.req("GET", "/events", raw=True)[0] == 503  # 서버가 SSE 를 거부한다
        # 큐를 멈춰 두어 wait 가 잡이 끝나기 전에 시작되게 한다
        # (빠른 러너에서 먼저 끝나면 SSE 를 열 기회가 없다)
        assert server.req("POST", "/pause", token="admin", json_body={})[0] == 200
        jid = run_ok_job(server)
        result: dict = {}
        waiter = threading.Thread(
            target=lambda: result.update(code=main(["wait", "--job", str(jid)]))
        )
        waiter.start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not calls:
            time.sleep(0.05)
        assert calls  # SSE 를 시도는 했고 503 을 받고 폴링으로 넘어갔다
        assert server.req("POST", "/resume", token="admin", json_body={})[0] == 200
        waiter.join(timeout=30)
        assert result.get("code") == 0
        out, _ = capsys.readouterr()
        assert last_json(out)["state"] == "succeeded"
    finally:
        server.close()


def test_wait_poll_flag_never_opens_sse(live, env, capsys, monkeypatch):
    calls: list = []

    def refuse(self, *args, **kwargs):
        calls.append(args)
        raise AssertionError("--poll must not open an event stream")

    monkeypatch.setattr(Client, "events", refuse)
    env(live)
    jid = run_ok_job(live)
    code, out, _ = run(capsys, ["wait", "--job", str(jid), "--poll"])
    assert code == 0 and last_json(out)["state"] == "succeeded"
    assert calls == []


def test_wait_reopens_sse_after_quiet_period(live, env, capsys, monkeypatch):
    """결정 F: 5초 동안 이벤트가 없으면 한 번 재조회하고 스트림을 다시 연다."""
    calls = count_events(monkeypatch)
    env(live)
    jid = run_ok_job(live, preset="slow")  # 첫 마커 뒤로는 조용하다
    live.wait_state(jid, "running")

    def cancel_once_reopened() -> None:
        deadline = time.monotonic() + 9
        while time.monotonic() < deadline and len(calls) < 2:
            time.sleep(0.05)
        live.req("POST", f"/jobs/{jid}/cancel", token="alice", json_body={})

    canceller = threading.Thread(target=cancel_once_reopened)
    canceller.start()
    code, out, _ = run(capsys, ["wait", "--job", str(jid)])
    canceller.join(timeout=5)
    assert code == 2 and last_json(out)["state"] == "cancelled"
    assert len(calls) >= 2, calls  # 조용한 5초 뒤 재연결


def test_wait_timeout_is_honoured_on_the_sse_path(live, env, capsys):
    env(live)
    jid = run_ok_job(live, preset="slow")
    live.wait_state(jid, "running")
    t0 = time.monotonic()
    code, out, err = run(capsys, ["wait", "--job", str(jid), "--timeout", "1"])
    assert code == 3 and "--timeout" in err
    assert last_json(out)["state"] in ("running", "cancelling")
    assert time.monotonic() - t0 < 10  # 늦어도 다음 5초 틱에서 본다
    live.req("POST", f"/jobs/{jid}/cancel", token="alice", json_body={})
    live.wait_terminal(jid)


def test_wait_missing_job_is_3_not_2(srv, env, capsys):
    env(srv)
    code, _, err = run(capsys, ["wait", "--job", "999"])
    assert code == 3 and "not found" in err
