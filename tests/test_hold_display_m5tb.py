"""M5t-b PR3 — 그룹 홀드의 사람용 화면(docs/m5tb-workplan.md §4.8 · 결정 330 · 350).

홀드의 기계 판독 표면(`needs_action` · `action.code = irreversible_hold` · `blocked_by` ·
`irreversible_holds`)은 PR1 이 만들었다. 여기서는 사람이 읽는 줄만 잠근다.

- `rcm wait`: 기다리는 잡이 홀드에 막히면 stderr 한 줄(원문 코드 없이 그룹 · 홀드 잡 · 풀 명령),
  `--return-on-action` 이면 4.
- `rcm top`: 막힌 대기 행의 이유 줄도 같은 말이다.
- `rcm check`: 홀드마다 경고 줄(warn, 종료 코드는 그대로). 홀드를 못 읽었으면(null) 경고 한 줄.
- `rcm doctor`: 역할이 worker · coordinator-worker 이면 이 머신의 홀드를 `needs action` 줄로,
  `--json` 에 `irreversible_holds`(이 머신의 것만, 모르면 null). 종료 코드 규칙은 그대로다.

시나리오: RL45(PR3 몫).
"""

from __future__ import annotations

import functools
from typing import Any

import pytest

import test_doctor_cli as doctor_tests
from fake_rcm_http import FakeRcm
from remote_ci_monitor import __version__
from remote_ci_monitor import cli as cli_mod
from remote_ci_monitor import client as client_mod
from remote_ci_monitor.cli import main
from remote_ci_monitor.core.render_text import render
from test_cli_m1 import last_json
from test_doctor_cli import CLIENT_TOKEN, TOP_KEYS, run, run_json, server_toml

# `rcm doctor` 시험의 픽스처를 이 파일에도 등록한다 — pytest 는 픽스처 정의의 이름으로 찾는다
env = doctor_tests.env
srv = doctor_tests.srv
svcbin = doctor_tests.svcbin
worker = doctor_tests.worker
coordinator = doctor_tests.coordinator

HOLD_A = {
    "job_id": 812,
    "preset": "ship",
    "group": "irreversible:ship",
    "scope": "global",
    "worker": "mbp",
    "cause": "worker_unreachable",
    "since": "2026-11-27T08:50:00Z",
}
HOLD_B = {
    "job_id": 815,
    "preset": "deploy",
    "group": "devices",
    "scope": "pool",
    "worker": None,
    "cause": "worker_restarted_without_job",
    "since": "2026-11-27T08:55:00Z",
}
HOLD_OTHER = {**HOLD_A, "job_id": 816, "worker": "build-02", "group": "release"}

ACTION = {
    "code": "irreversible_hold",
    "note": "group held — run rcm release-hold 812",
    "at": "2026-11-27T08:50:00Z",
}
BLOCKED = {"job_id": 812, "group": "irreversible:ship", "remaining_seconds": None}
RAW = ("irreversible_hold", "worker_unreachable", "worker_restarted_without_job")


def no_raw_code(text: str) -> None:
    for code in RAW:
        assert code not in text, (code, text)


# ── rcm wait ─────────────────────────────────────────────────────────────────

JOB = 821


class Script:
    def __init__(self, fake: FakeRcm):
        self.fake = fake
        self.docs: list[dict[str, Any]] = []
        fake.route("GET", "/api/health", {"ok": True, "version": __version__})
        fake.route("GET", f"/jobs/{JOB}", lambda _b: self.next_doc())

    def next_doc(self) -> dict[str, Any]:
        return self.docs.pop(0) if len(self.docs) > 1 else self.docs[0]


def queued(held: bool = True, **extra: Any) -> dict[str, Any]:
    d: dict[str, Any] = {
        "id": JOB,
        "state": "queued",
        "preset": "ship",
        "position": 1,
        "exit_code": None,
        "url": f"http://build.example:8787/#/jobs/{JOB}",
        "keep": False,
        "shelf": None,
        "reason": "needs_action" if held else "waiting_for_lane",
        "blocked_by": BLOCKED if held else None,
        "action": ACTION if held else None,
        "estimate": {"finish_at": None, "wait_seconds": None},
    }
    d.update(extra)
    return d


def finished() -> dict[str, Any]:
    return {**queued(False), "state": "succeeded", "exit_code": 0, "reason": None}


@pytest.fixture
def fake(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("XDG_CONFIG_HOME", "RCM_LABEL", "RCM_CONFIG"):
        monkeypatch.delenv(var, raising=False)
    srv_ = FakeRcm()
    monkeypatch.setenv("RCM_SERVER", srv_.url)
    monkeypatch.setenv("RCM_TOKEN", "alice-token")
    fast = functools.partial(client_mod.wait_for_job, poll_seconds=0.01)
    monkeypatch.setattr(cli_mod, "wait_for_job", fast)
    yield Script(srv_)
    srv_.close()


def cli_run(capsys, argv: list[str]) -> tuple[int, str, str]:
    try:
        code = main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 2
    cap = capsys.readouterr()
    return code, cap.out, cap.err


HELD_LINE = (
    f"#{JOB} needs action · group irreversible:ship is held by job #812 — check that machine "
    "for a leftover process, then ask an admin to run rcm release-hold 812"
)


def test_rl45_rcm_wait_says_in_words_that_the_group_is_held(fake, capsys):
    fake.docs = [queued(), queued(), queued(held=False), finished()]
    code, _out, err = cli_run(capsys, ["wait", "--job", str(JOB), "--poll"])
    assert code == 0, err
    # 진행 줄(`#821 queued · 1st in line · needs action`)은 오늘 그대로다 — 남는 줄만 센다
    assert [ln for ln in err.splitlines() if "needs action ·" in ln] == [HELD_LINE], err
    no_raw_code(err)


def test_rl45_return_on_action_ends_4_on_a_hold(fake, capsys):
    fake.docs = [queued(), finished()]
    code, out, err = cli_run(capsys, ["wait", "--job", str(JOB), "--poll", "--return-on-action"])
    assert code == 4, err
    assert HELD_LINE in err.splitlines(), err
    doc = last_json(out)
    assert doc["wait_exit_code"] == 4 and doc["action"]["code"] == "irreversible_hold"


def test_rl45_a_hold_action_without_blocked_by_falls_back_to_the_plain_line(fake, capsys):
    """옛 · 가짜 서버의 문서 — 그룹을 모르면 지어내지 않고 오늘의 줄 그대로다."""
    fake.docs = [queued(blocked_by=None), finished()]
    code, _out, err = cli_run(capsys, ["wait", "--job", str(JOB), "--poll"])
    assert code == 0, err
    assert (
        f"#{JOB} needs action · irreversible_hold · group held — run rcm release-hold 812"
        in err.splitlines()
    ), err


# ── rcm top ──────────────────────────────────────────────────────────────────


def test_rl45_rcm_top_reason_line_of_a_held_waiting_job_has_no_raw_code():
    row = {
        **queued(),
        "pool": "default",
        "key": "ship",
        "requester": {"name": "alice", "label": "alice@mbp"},
        "joiners": [],
        "source": {"mode": "tree"},
        "placement": "pool",
        "placement_detail": [],
        "estimate": {"waited_seconds": 30.0, "source": "preset", "sample_count": 0},
    }
    doc = {
        "schema_version": 1,
        "generated_at": "2026-11-27T09:00:00Z",
        "display_timezone": "UTC",
        "server": {"lanes": 1, "workers": []},
        "pools": [{"name": "default", "queue": [row], "recent": [], "medians": {}, "hosts": []}],
    }
    text = render(doc)
    lines = text.splitlines()
    i = next(n for n, ln in enumerate(lines) if f"#{JOB} " in ln)
    assert lines[i + 1].strip() == (
        "needs action · group irreversible:ship is held by #812 — rcm release-hold 812 after "
        "checking the machine"
    ), text
    no_raw_code(lines[i + 1])


# ── rcm check ────────────────────────────────────────────────────────────────


def check_lines(fake: Script, capsys, monkeypatch, health_extra: dict[str, Any]) -> tuple:
    fake.fake.route("GET", "/api/health", {"ok": True, "version": __version__, **health_extra})
    fake.fake.route("GET", "/api/whoami", {"name": "alice", "admin": False})
    fake.fake.route(
        "GET", "/api/status", {"presets": [{"name": "ship"}], "pools": [], "server": {}}
    )

    def no_local_config(*_a, **_k):
        from remote_ci_monitor.config import ConfigError

        raise ConfigError("no server config here")

    monkeypatch.setattr(cli_mod, "load_server_config", no_local_config)
    code, out, _err = cli_run(capsys, ["check"])
    return code, [ln for ln in out.splitlines() if "irreversible hold" in ln]


def test_rl45_rcm_check_warns_once_per_hold_and_keeps_its_exit_code(fake, capsys, monkeypatch):
    code0, base = check_lines(fake, capsys, monkeypatch, {"irreversible_holds": []})
    assert base == []
    code, lines = check_lines(fake, capsys, monkeypatch, {"irreversible_holds": [HOLD_A, HOLD_B]})
    assert code == code0  # 경고다 — FAIL(종료 1)이 아니다
    assert lines == [
        "warn  irreversible hold group irreversible:ship · job #812 (ship) on mbp closed without "
        "a stop confirmation — check mbp, then rcm release-hold 812",
        "warn  irreversible hold group devices · job #815 (deploy) on the server's local lane "
        "closed without a stop confirmation — check the server, then rcm release-hold 815",
    ], lines
    for ln in lines:
        no_raw_code(ln)


def test_rl45_rcm_check_says_it_could_not_read_the_holds(fake, capsys, monkeypatch):
    code0, _ = check_lines(fake, capsys, monkeypatch, {"irreversible_holds": []})
    code, lines = check_lines(fake, capsys, monkeypatch, {"irreversible_holds": None})
    assert code == code0
    assert lines == [
        "warn  irreversible hold the server could not read its group holds — unknown"
    ], lines


UNREADABLE_HOLDS = (
    {"job_id": "812", "group": "irreversible:ship"},  # 번호가 문자열
    {"job_id": 813, "group": ""},  # 그룹이 비었다
    {"job_id": True, "group": "devices"},  # bool 은 번호가 아니다
    "not a hold",
)


@pytest.mark.parametrize("bad", UNREADABLE_HOLDS, ids=["str-id", "no-group", "bool-id", "str"])
def test_rl45_rcm_check_says_unknown_when_one_hold_cannot_be_read(fake, capsys, monkeypatch, bad):
    """# RL45 읽을 수 없는 항목을 조용히 빼면 짧은 목록이 「그게 전부」로 읽힌다. `rcm doctor` 의
    `parse_holds` 와 웹의 `holdsOf` 와 같은 규칙: 하나라도 못 읽으면 목록 전체가 모름이다(P3 r1)."""
    code0, _ = check_lines(fake, capsys, monkeypatch, {"irreversible_holds": []})
    code, lines = check_lines(fake, capsys, monkeypatch, {"irreversible_holds": [HOLD_A, bad]})
    assert code == code0
    assert lines == [
        "warn  irreversible hold could not read every group hold the server sent — unknown"
    ], lines


def test_rl45_doctor_json_is_null_when_one_hold_cannot_be_read(worker, srv, capsys):
    """# RL45 doctor 의 `parse_holds` 는 못 읽는 항목 하나에 목록 전체를 모름(null)으로 둔다 —
    `rcm check` · 웹과 같은 규칙."""
    srv.route(
        "GET",
        "/api/health",
        {"ok": True, "version": __version__, "irreversible_holds": [HOLD_A, {"job_id": "x"}]},
    )
    _code, doc = worker(capsys)
    assert doc["irreversible_holds"] is None, doc["irreversible_holds"]


def test_rl45_rcm_check_has_no_hold_row_on_an_old_server(fake, capsys, monkeypatch):
    _code, lines = check_lines(fake, capsys, monkeypatch, {})
    assert lines == []


# ── rcm doctor ───────────────────────────────────────────────────────────────

DOCTOR_LINE_A = (
    "needs action  job #812 (ship) · group irreversible:ship: a job that ran here was closed "
    "without a stop confirmation — check for a leftover process, then ask an admin to run "
    "rcm release-hold 812"
)


def test_rl45_doctor_json_has_the_key_and_lists_only_this_machines_holds(worker, srv, capsys):
    code0, doc0 = worker(capsys)
    assert set(doc0) == TOP_KEYS | {"irreversible_holds"}
    assert doc0["irreversible_holds"] is None  # 서버가 키를 싣지 않았다(옛 서버) — 모른다
    srv.route(
        "GET",
        "/api/health",
        {"ok": True, "version": __version__, "irreversible_holds": [HOLD_A, HOLD_OTHER]},
    )
    code, doc = worker(capsys)
    assert code == code0  # 종료 코드 규칙은 그대로다
    assert doc["irreversible_holds"] == [HOLD_A], doc["irreversible_holds"]
    assert doc["doctor_version"] == 1


def test_rl45_doctor_prints_a_needs_action_line_for_this_machine(worker, srv, capsys):
    srv.route(
        "GET",
        "/api/health",
        {"ok": True, "version": __version__, "irreversible_holds": [HOLD_A, HOLD_OTHER]},
    )
    _code, out, _err = worker.text(capsys)
    lines = [ln for ln in out.splitlines() if ln.startswith("needs action")]
    assert lines == [DOCTOR_LINE_A], out
    no_raw_code("\n".join(lines))


def test_rl45_doctor_with_no_hold_here_prints_nothing_and_has_an_empty_list(worker, srv, capsys):
    srv.route("GET", "/api/health", {"ok": True, "version": __version__, "irreversible_holds": []})
    _code, doc = worker(capsys)
    assert doc["irreversible_holds"] == []
    _code, out, _err = worker.text(capsys)
    assert "needs action" not in out


HOLDS_UNKNOWN = "group holds: unknown — "


def test_rl45_doctor_text_says_the_holds_are_unknown_on_a_worker(worker, srv, capsys):
    """# RL45 P3 r1: 텍스트 출력에서 null(모름)과 [](없음)이 둘 다 아무것도 안 찍으면 모름이
    「없음」으로 읽힌다. 홀드를 가질 수 있는 역할이면 null 에 한 줄."""
    _code, doc = worker(capsys)
    assert doc["irreversible_holds"] is None  # 옛 서버 — 키가 없다
    _code, out, _err = worker.text(capsys)
    lines = [ln for ln in out.splitlines() if HOLDS_UNKNOWN in ln]
    assert len(lines) == 1, out
    srv.route("GET", "/api/health", {"ok": True, "version": __version__, "irreversible_holds": []})
    _code, out, _err = worker.text(capsys)
    assert HOLDS_UNKNOWN not in out  # 없음은 조용하다


def test_rl45_doctor_text_says_unknown_on_a_coordinator_worker(
    env, coordinator, monkeypatch, capsys
):
    monkeypatch.setenv("RCM_TOKEN", CLIENT_TOKEN)
    health = coordinator.routes[("GET", "/api/health")][1]
    coordinator.route("GET", "/api/health", {**health, "irreversible_holds": None})
    cfg = server_toml(env, coordinator.port)
    _code, out, _err = run(capsys, "--role", "coordinator-worker", "--config", str(cfg))
    assert len([ln for ln in out.splitlines() if HOLDS_UNKNOWN in ln]) == 1, out


def test_rl45_doctor_text_is_quiet_about_holds_on_a_client(env, coordinator, monkeypatch, capsys):
    """client · coordinator 역할의 null 은 「이 머신과 무관」이다 — 지금처럼 조용히."""
    monkeypatch.setenv("RCM_TOKEN", CLIENT_TOKEN)
    _code, out, _err = run(capsys, "--role", "client", "--server", coordinator.url)
    assert HOLDS_UNKNOWN not in out, out


def test_rl45_coordinator_worker_sees_the_local_lane_holds(env, coordinator, monkeypatch, capsys):
    monkeypatch.setenv("RCM_TOKEN", CLIENT_TOKEN)
    health = coordinator.routes[("GET", "/api/health")][1]
    coordinator.route("GET", "/api/health", {**health, "irreversible_holds": [HOLD_A, HOLD_B]})
    cfg = server_toml(env, coordinator.port)
    code0, doc = run_json(capsys, "--role", "coordinator-worker", "--config", str(cfg))
    assert doc["irreversible_holds"] == [HOLD_B], doc["irreversible_holds"]
    _code, out, _err = run(capsys, "--role", "coordinator-worker", "--config", str(cfg))
    assert [ln for ln in out.splitlines() if ln.startswith("needs action")] == [
        "needs action  job #815 (deploy) · group devices: a job that ran here was closed without "
        "a stop confirmation — check for a leftover process, then ask an admin to run "
        "rcm release-hold 815"
    ], out


def test_rl45_client_role_has_a_null_hold_list(env, coordinator, monkeypatch, capsys):
    monkeypatch.setenv("RCM_TOKEN", CLIENT_TOKEN)
    health = coordinator.routes[("GET", "/api/health")][1]
    coordinator.route("GET", "/api/health", {**health, "irreversible_holds": [HOLD_B]})
    code, doc = run_json(capsys, "--role", "client", "--server", coordinator.url)
    assert code == 0 and doc["irreversible_holds"] is None, doc
