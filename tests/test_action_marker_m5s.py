"""M5s PR C — 「사람이 할 일」 표식 `::rcm::action::<code>::<note>` (명세 §4.3 · 결정 213 · 234).

서버·CLI 쪽만 잠근다(웹 표시는 G, 예약 행의 이유와 사슬 기준 표식은 D·G).

- 파싱: `code` 는 `[a-z0-9_-]{1,32}`, `note` 는 200자 · 제어문자 제거, 어긋나면 마커가 아니다.
- 저장: 이벤트(마커)로 남아 잡이 끝나도 보인다. 원격 워커의 로그도 서버가 같은 길로 파싱한다.
- 잡 JSON 의 `action {code, note, at}` — 마지막 표식 하나. `GET /jobs/{id}` · `/api/status`
  의 큐 행과 최근 행 · `rcm wait` 최종 JSON.
- 도는 행에 표식이 있으면 이유 `needs_action`(행동 가능한 이유의 맨 앞).
- `rcm wait` 는 새 표식을 stderr 한 줄로, `--return-on-action` 이면 4 로 돌아온다(잡은 계속 돈다).
- `[[notify]]` env `RCM_ACTION_CODE` · `RCM_ACTION_NOTE`.
- `rcm check` 는 `server.notify_failures` 가 0 이 아니면 warn 행.

시나리오: §7 S12(서버·CLI 쪽) · S25.
"""

from __future__ import annotations

import functools
import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from fake_rcm_http import FakeRcm
from remote_ci_monitor import __version__
from remote_ci_monitor import cli as cli_mod
from remote_ci_monitor import client as client_mod
from remote_ci_monitor.cli import main
from remote_ci_monitor.core import model
from remote_ci_monitor.core.model import (
    CANCELLING,
    RUNNING,
    Action,
    Job,
    Requester,
    Source,
)
from remote_ci_monitor.core.notify import notify_env
from remote_ci_monitor.core.progress import (
    KIND_ACTION,
    MARKER_KINDS,
    MAX_ACTION_NOTE,
    Marker,
    markers_from_log,
    parse_marker,
    progress_from_markers,
)
from remote_ci_monitor.core.queue import QueueConfig, compute_queue
from remote_ci_monitor.core.status import action_json, iso, queue_row_json, recent_json
from test_cli_m1 import last_json

T0 = datetime(2026, 11, 23, 6, 0, 0, tzinfo=UTC)


# ── 파싱 ─────────────────────────────────────────────────────────────────────


def test_action_is_a_marker_kind():
    assert KIND_ACTION == "action"
    assert KIND_ACTION in MARKER_KINDS
    assert MAX_ACTION_NOTE == 200


def test_parse_reads_code_and_note():
    assert parse_marker("::rcm::action::login::Sign in on the build machine\n") == (
        KIND_ACTION,
        "login::Sign in on the build machine",
    )


def test_parse_keeps_double_colons_inside_the_note():
    assert parse_marker("::rcm::action::login::step 2::open the profile") == (
        KIND_ACTION,
        "login::step 2::open the profile",
    )


def test_parse_accepts_a_code_without_a_note():
    assert parse_marker("::rcm::action::login") == (KIND_ACTION, "login")
    assert parse_marker("::rcm::action::login::") == (KIND_ACTION, "login")
    assert parse_marker("::rcm::action::login::   ") == (KIND_ACTION, "login")


@pytest.mark.parametrize(
    "code",
    ["", "Login", "log in", "로그인", "a" * 33, "login!", "a.b", "a/b", "\x1b[31m"],
)
def test_parse_refuses_a_code_outside_the_vocabulary(code):
    assert parse_marker(f"::rcm::action::{code}::note") is None


@pytest.mark.parametrize("code", ["a", "login", "re-auth", "two_factor", "x9", "a" * 32])
def test_parse_accepts_every_code_in_the_vocabulary(code):
    assert parse_marker(f"::rcm::action::{code}::n") == (KIND_ACTION, f"{code}::n")


def test_parse_strips_control_characters_from_the_note():
    kind, value = parse_marker("::rcm::action::login::sign\r\x1b[2Kin\x85  now\x00")
    assert kind == KIND_ACTION
    assert value == "login::sign[2Kin now"


def test_parse_cuts_the_note_at_200_characters():
    kind, value = parse_marker("::rcm::action::login::" + "x" * 500)
    assert kind == KIND_ACTION and value == "login::" + "x" * 200


def test_parse_needs_the_line_start():
    assert parse_marker(" ::rcm::action::login::x") is None
    assert parse_marker("::rcm::action") is None


def test_progress_keeps_the_last_action_with_its_receive_time():
    marks = markers_from_log(
        [
            (T0, "::rcm::step::publish"),
            (T0 + timedelta(seconds=3), "::rcm::action::login::first"),
            (T0 + timedelta(seconds=9), "::rcm::action::two_factor::second"),
        ]
    )
    p = progress_from_markers(marks, started_at=T0, finished_at=None, now=T0, exit_code=None)
    assert p.action == Action(code="two_factor", note="second", at=T0 + timedelta(seconds=9))


def test_progress_without_an_action_has_none():
    marks = [Marker(at=T0, kind="step", value="a")]
    p = progress_from_markers(marks, started_at=T0, finished_at=None, now=T0, exit_code=None)
    assert p.action is None


# ── 이유 `needs_action` ──────────────────────────────────────────────────────


def test_needs_action_is_a_wire_value_and_leads_the_actionable_reasons():
    assert model.REASON_NEEDS_ACTION == "needs_action"
    assert model.ACTIONABLE_REASONS[0] == "needs_action"
    # 나머지 차례는 그대로다
    assert model.ACTIONABLE_REASONS[1:] == (
        "worker_down",
        "no_capable_machine",
        "check_failed",
        "stuck",
        "upload_stalled",
        "not_scheduled",
        "blocked_by_group",
        "overdue",
        "paused",
    )


def busy(job_id: int, *, state: str = RUNNING, started: datetime = T0) -> Job:
    return Job(
        id=job_id,
        preset="golive",
        inputs={},
        key="golive",
        concurrency_group=None,
        source=Source(mode="git_ref", ref="main", sha="0" * 40),
        requester=Requester(name="alice-laptop", label="alice@laptop"),
        state=state,
        created_at=started,
        queued_at=started,
        started_at=started,
        lane=1,
        phase="executing",
        last_output_at=started,
    )


def rows_for(jobs: list[Job], markers: dict[int, list[Marker]], now: datetime):
    progress = {
        j.id: progress_from_markers(
            markers.get(j.id, []),
            started_at=j.started_at,
            finished_at=None,
            now=now,
            exit_code=None,
        )
        for j in jobs
    }
    workers = [
        model.WorkerInfo(lane=i + 1, state="busy", job_id=j.id, since=T0)
        for i, j in enumerate(jobs)
    ]
    return compute_queue(
        jobs,
        workers=workers,
        paused=False,
        medians={},
        presets={},
        cfg=QueueConfig(),
        now=now,
        progress=progress,
    )


def test_a_running_row_with_an_action_needs_action():
    now = T0 + timedelta(seconds=30)
    marks = {1: [Marker(at=T0, kind=KIND_ACTION, value="login::Sign in")]}
    (row,) = rows_for([busy(1)], marks, now)
    assert row.reason == "needs_action"
    assert row.action == Action(code="login", note="Sign in", at=T0)
    doc = queue_row_json(row)
    assert doc["action"] == {"code": "login", "note": "Sign in", "at": iso(T0)}


def test_a_running_row_without_an_action_keeps_its_reason():
    now = T0 + timedelta(seconds=30)
    (row,) = rows_for([busy(1)], {}, now)
    assert row.reason == "running" and row.action is None
    assert queue_row_json(row)["action"] is None


def test_needs_action_beats_stuck():
    """표식을 찍고 사람을 기다리는 잡은 조용하다 — 그 침묵의 이유가 표식이다."""
    now = T0 + timedelta(hours=5)  # 기본 기대 시간의 몇 배 — 멈춤 판정이 걸린다
    marks = {1: [Marker(at=T0, kind=KIND_ACTION, value="login")]}
    (row,) = rows_for([busy(1)], marks, now)
    assert row.estimate.stuck or row.estimate.overdue  # 장면이 비어 있지 않다
    assert row.reason == "needs_action"


def test_cancelling_beats_needs_action():
    now = T0 + timedelta(seconds=30)
    marks = {1: [Marker(at=T0, kind=KIND_ACTION, value="login")]}
    (row,) = rows_for([busy(1, state=CANCELLING)], marks, now)
    assert row.reason == "cancelling"


# ── 잡 JSON 모양 ─────────────────────────────────────────────────────────────


def test_action_json_shape():
    assert action_json(None) is None
    assert action_json(Action(code="login", note=None, at=T0)) == {
        "code": "login",
        "note": None,
        "at": iso(T0),
    }


def test_recent_json_carries_the_action_it_is_given():
    job = busy(3)
    assert recent_json(job)["action"] is None
    doc = recent_json(job, action=Action(code="login", note="n", at=T0))
    assert doc["action"] == {"code": "login", "note": "n", "at": iso(T0)}


# ── notify env ───────────────────────────────────────────────────────────────


def test_notify_env_passes_the_action():
    env = notify_env({"id": 1, "action": {"code": "login", "note": "Sign in\x1b", "at": "x"}}, "h")
    assert env["RCM_ACTION_CODE"] == "login"
    assert env["RCM_ACTION_NOTE"] == "Sign in"


def test_notify_env_without_an_action_is_empty():
    env = notify_env({"id": 1}, "h")
    assert env["RCM_ACTION_CODE"] == "" and env["RCM_ACTION_NOTE"] == ""
    env = notify_env({"id": 1, "action": None}, "h")
    assert env["RCM_ACTION_CODE"] == "" and env["RCM_ACTION_NOTE"] == ""


# ── 서버: 로컬 워커 (S12) ────────────────────────────────────────────────────


ACTION_LINE = "::rcm::action::login::Sign in on the build machine"


ACTION_PRESETS = [
    ("needs", f"echo '::rcm::step::publish'; echo '{ACTION_LINE}'; sleep 20"),
    ("asked", f"echo '{ACTION_LINE}'; echo '::rcm::summary::asked'; exit 75"),
    ("ok", "echo '::rcm::step::a'; exit 0"),
]


def action_server(tmp_path, monkeypatch, *, start: bool = True):
    import test_server as ts

    monkeypatch.setattr(ts, "PRESETS", [ts.sh(n, script) for n, script in ACTION_PRESETS])
    return ts.Server(tmp_path, workers=start)


@pytest.fixture
def live(tmp_path, monkeypatch):
    s = action_server(tmp_path, monkeypatch)
    yield s
    s.close()


def submitted(live, preset: str) -> int:
    status, resp = live.submit(preset=preset)
    assert status == 201, resp
    jid = int(resp["job_id"])
    assert live.upload(jid)[0] == 200
    return jid


def wait_for(predicate, timeout: float = 15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        got = predicate()
        if got:
            return got
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


def test_a_running_job_shows_its_action_in_the_job_view_and_the_queue(live):
    jid = submitted(live, "needs")
    doc = wait_for(lambda: (d := live.req("GET", f"/jobs/{jid}")[1]) and d.get("action") and d)
    assert doc["state"] == "running"
    assert doc["action"]["code"] == "login"
    assert doc["action"]["note"] == "Sign in on the build machine"
    assert doc["action"]["at"].endswith("Z")
    assert doc["reason"] == "needs_action"
    _, status = live.req("GET", "/api/status")
    (row,) = [r for r in status["pools"][0]["queue"] if r["id"] == jid]
    assert row["action"] == doc["action"] and row["reason"] == "needs_action"
    live.req("POST", f"/jobs/{jid}/cancel", token="admin", json_body={})


def test_a_finished_job_keeps_its_action(live):
    jid = submitted(live, "asked")
    live.wait_terminal(jid)
    _, doc = live.req("GET", f"/jobs/{jid}")
    assert doc["state"] == "failed" and doc["exit_code"] == 75
    assert doc["action"]["code"] == "login"
    assert doc["action"]["note"] == "Sign in on the build machine"
    _, status = live.req("GET", "/api/status")
    (row,) = [r for r in status["pools"][0]["recent"] if r["id"] == jid]
    assert row["action"] == doc["action"]


def test_a_job_without_an_action_has_null(live):
    jid = submitted(live, "ok")
    live.wait_terminal(jid)
    _, doc = live.req("GET", f"/jobs/{jid}")
    assert "action" in doc and doc["action"] is None
    _, status = live.req("GET", "/api/status")
    (row,) = [r for r in status["pools"][0]["recent"] if r["id"] == jid]
    assert row["action"] is None


def test_the_notify_hook_gets_the_action_env(tmp_path, monkeypatch):
    from remote_ci_monitor.core.notify import NotifyRule

    srv = action_server(tmp_path, monkeypatch, start=False)
    try:
        srv.app.config.notify = (NotifyRule(name="file", argv=("true",)),)
        srv.app.start()
        seen: list[dict[str, str]] = []

        class Done:
            returncode = 0

        def hook(argv, **kw):
            seen.append(dict(kw["env"]))
            return Done()

        srv.app.notifier.run = hook
        jid = submitted(srv, "asked")
        srv.wait_terminal(jid)
        wait_for(lambda: seen)
        assert seen[0]["RCM_JOB_ID"] == str(jid)
        assert seen[0]["RCM_ACTION_CODE"] == "login"
        assert seen[0]["RCM_ACTION_NOTE"] == "Sign in on the build machine"
    finally:
        srv.close()


# ── 서버: 원격 워커 (같은 파싱 길) ───────────────────────────────────────────


def test_a_remote_worker_action_is_parsed_by_the_server(tmp_path):
    from test_worker_api import WorkerServer, running_job

    srv = WorkerServer(tmp_path)
    try:
        jid = running_job(srv)
        assert srv.phase("build-02", jid, "executing")[0] == 200
        assert srv.log("build-02", jid, (ACTION_LINE + "\n").encode())[0] == 200
        row = srv.row(jid)
        assert row["reason"] == "needs_action"
        assert row["action"] == {
            "code": "login",
            "note": "Sign in on the build machine",
            "at": iso(srv.clock()),
        }
        assert srv.finish("build-02", jid, "failed", exit_code=75)[0] == 200
        _, doc = srv.req("GET", f"/jobs/{jid}")
        assert doc["action"]["code"] == "login"
    finally:
        srv.close()


# ── rcm wait (가짜 서버) ─────────────────────────────────────────────────────

JOB = 41


class Script:
    """잡 문서를 차례대로 내주는 가짜 서버(마지막 것은 계속)."""

    def __init__(self, fake: FakeRcm):
        self.fake = fake
        self.docs: list[dict[str, Any]] = []
        fake.route("GET", "/api/health", {"ok": True, "version": __version__})
        fake.route("GET", f"/jobs/{JOB}", lambda _b: self.next_doc())

    def next_doc(self) -> dict[str, Any]:
        return self.docs.pop(0) if len(self.docs) > 1 else self.docs[0]


def job_doc(state: str = "running", action: dict | None = None, **extra: Any) -> dict[str, Any]:
    doc = {
        "id": JOB,
        "state": state,
        "preset": "golive",
        "exit_code": None if state == "running" else (0 if state == "succeeded" else 1),
        "url": f"http://build.example:8787/#/jobs/{JOB}",
        "keep": False,
        "shelf": None,
        "reason": "needs_action" if action and state == "running" else "running",
        "action": action,
    }
    doc.update(extra)
    return doc


LOGIN = {"code": "login", "note": "Sign in on the build machine", "at": "2026-11-23T06:00:03Z"}
LOGIN_AGAIN = {**LOGIN, "at": "2026-11-23T06:10:03Z"}


@pytest.fixture
def fake(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("XDG_CONFIG_HOME", "RCM_LABEL", "RCM_CONFIG"):
        monkeypatch.delenv(var, raising=False)
    srv = FakeRcm()
    monkeypatch.setenv("RCM_SERVER", srv.url)
    monkeypatch.setenv("RCM_TOKEN", "alice-token")
    fast = functools.partial(client_mod.wait_for_job, poll_seconds=0.01)
    monkeypatch.setattr(cli_mod, "wait_for_job", fast)
    yield Script(srv)
    srv.close()


def run(capsys, argv: list[str]) -> tuple[int, str, str]:
    try:
        code = main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 2
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def test_wait_prints_one_line_when_an_action_arrives(fake, capsys):
    fake.docs = [job_doc(), job_doc(action=LOGIN), job_doc(action=LOGIN), job_doc("failed", LOGIN)]
    code, out, err = run(capsys, ["wait", "--job", str(JOB), "--poll"])
    assert code == 1, err
    lines = [ln for ln in err.splitlines() if "needs action" in ln]
    assert lines == [f"#{JOB} needs action · login · Sign in on the build machine"], err
    assert last_json(out)["action"] == LOGIN


def test_wait_without_the_flag_does_not_return_on_an_action(fake, capsys):
    fake.docs = [job_doc(action=LOGIN)] * 3 + [job_doc("succeeded", LOGIN)]
    code, out, _err = run(capsys, ["wait", "--job", str(JOB), "--poll"])
    assert code == 0
    assert last_json(out)["wait_exit_code"] == 0


def test_return_on_action_exits_4_and_the_json_has_the_action(fake, capsys):
    fake.docs = [job_doc(), job_doc(), job_doc(action=LOGIN), job_doc("succeeded")]
    code, out, err = run(capsys, ["wait", "--job", str(JOB), "--poll", "--return-on-action"])
    assert code == 4, err
    doc = last_json(out)
    assert doc["wait_exit_code"] == 4
    assert doc["state"] == "running" and doc["action"] == LOGIN
    assert f"rcm wait --job {JOB}" in err  # 다시 기다리는 길
    assert "log:" not in err.lower()  # 실패 안내(로그 길)를 붙이지 않는다 — 실패가 아니다


def test_return_on_action_returns_at_once_on_an_action_already_there(fake, capsys):
    """붙은 세션은 표식이 찍힌 뒤에 오는 것이 보통이다 — 이미 있던 표식을 건너뛰면 사람에게 알릴
    순간을 놓친다. 그래서 곧바로 4 이고, 다시 기다릴 때는 플래그 없이다(안내 줄이 그렇게 말한다)."""
    fake.docs = [job_doc(action=LOGIN)] * 3 + [job_doc("succeeded", LOGIN)]
    code, out, err = run(capsys, ["wait", "--job", str(JOB), "--poll", "--return-on-action"])
    assert code == 4, err
    assert last_json(out)["action"] == LOGIN
    assert f"`rcm wait --job {JOB}`" in err


def test_the_line_is_printed_once_per_action_and_again_for_a_newer_one(fake, capsys):
    fake.docs = [job_doc(action=LOGIN)] * 3 + [job_doc(action=LOGIN_AGAIN), job_doc("failed")]
    code, _out, err = run(capsys, ["wait", "--job", str(JOB), "--poll"])
    assert code == 1
    assert [ln for ln in err.splitlines() if "needs action" in ln] == [
        f"#{JOB} needs action · login · Sign in on the build machine"
    ] * 2


def test_an_action_code_outside_the_vocabulary_is_not_printed(fake, capsys):
    bad = {"code": "\x1b[2Jlogin", "note": "x", "at": "2026-11-23T06:00:03Z"}
    fake.docs = [job_doc(action=bad), job_doc("succeeded")]
    code, _out, err = run(capsys, ["wait", "--job", str(JOB), "--poll", "--return-on-action"])
    assert code == 0
    assert "needs action" not in err


def test_a_finished_job_wins_over_return_on_action(fake, capsys):
    fake.docs = [job_doc(), job_doc("failed", LOGIN)]
    code, out, _err = run(capsys, ["wait", "--job", str(JOB), "--poll", "--return-on-action"])
    assert code == 1
    assert last_json(out)["wait_exit_code"] == 1


def test_exit_4_needs_the_flag():
    """4 는 `--return-on-action` 일 때만 나온다(결정 234) — 상태 표에 4 는 없다."""
    assert 4 not in model.EXIT_CODE_BY_STATE.values()
    assert cli_mod.EXIT_ACTION == 4


# ── rcm wait --return-on-action (S25, 진짜 서버 · 이벤트 스트림) ────────────


def test_s25_return_on_action_against_a_real_server(live, monkeypatch, tmp_path, capsys):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("XDG_CONFIG_HOME", "RCM_LABEL", "RCM_CONFIG"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("RCM_SERVER", f"http://127.0.0.1:{live.port}")
    monkeypatch.setenv("RCM_TOKEN", live.tokens["alice"])
    jid = submitted(live, "needs")
    started = time.monotonic()
    code, out, err = run(capsys, ["wait", "--job", str(jid), "--return-on-action"])
    assert code == 4, err
    assert time.monotonic() - started < 15  # 잡이 끝날 때(20초 sleep)까지 기다리지 않았다
    doc = last_json(out)
    assert doc["action"]["code"] == "login" and doc["wait_exit_code"] == 4
    # 잡은 계속 돈다
    assert live.store.get_job(jid).state == RUNNING
    live.req("POST", f"/jobs/{jid}/cancel", token="admin", json_body={})


# ── rcm check: notify_failures ───────────────────────────────────────────────


def check_rows(fake: FakeRcm, notify_failures: Any) -> None:
    fake.route("GET", "/api/whoami", {"name": "alice", "admin": False})
    server: dict[str, Any] = {}
    if notify_failures is not ...:
        server["notify_failures"] = notify_failures
    fake.route("GET", "/api/status", {"presets": [{"name": "gate"}], "pools": [], "server": server})


def _no_local_config(*_a, **_k):
    from remote_ci_monitor.config import ConfigError

    raise ConfigError("no server config here")


def _check(capsys, monkeypatch, fake, value) -> list[str]:
    check_rows(fake.fake, value)
    monkeypatch.setattr(cli_mod, "load_server_config", _no_local_config)
    _code, out, _err = run(capsys, ["check"])
    return [ln for ln in out.splitlines() if " notify " in f" {ln} "]


def test_check_row_is_warn_with_the_count(fake, capsys, monkeypatch):
    (line,) = _check(capsys, monkeypatch, fake, 3)
    assert line.startswith("warn  notify")
    assert "3" in line and "restart" in line


def test_check_has_no_notify_row_when_nothing_failed(fake, capsys, monkeypatch):
    assert _check(capsys, monkeypatch, fake, 0) == []


def test_check_has_no_notify_row_on_an_old_server(fake, capsys, monkeypatch):
    assert _check(capsys, monkeypatch, fake, ...) == []
    assert _check(capsys, monkeypatch, fake, None) == []


def test_status_doc_json_is_serialisable_with_actions():
    doc = recent_json(busy(5), action=Action(code="login", note=None, at=T0))
    json.dumps(doc)
