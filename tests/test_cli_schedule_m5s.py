"""M5s A — CLI 의 예약: `rcm run --at` · `--in` · 옛 서버 거절(S11) · `rcm wait` · `rcm jobs` ·
`rcm eta`(`docs/m5s-workplan.md` §4.1).

서버는 `fake_rcm_http.FakeRcm` 이다. 시각 문자열은 서버가 해석한다(결정 207) — CLI 는 `--at` 을
그대로 `start_at` 으로 싣고, `--in` 은 초로 바꿔 `start_in_seconds` 로 싣는다. 어느 쪽도 이 머신의
시계로 절대 시각을 만들지 않는다.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from fake_rcm_http import FakeRcm
from remote_ci_monitor import __version__
from remote_ci_monitor import cli as cli_mod
from remote_ci_monitor.cli import build_parser, main, parse_duration
from remote_ci_monitor.client import Client
from test_cli_m1 import last_json

JOB = 77
NOT_BEFORE = "2026-11-23T06:00:00Z"


def run(capsys, argv: list[str]) -> tuple[int, str, str]:
    try:
        code = main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 2
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def scheduled_doc(**extra: Any) -> dict[str, Any]:
    return {
        "id": JOB,
        "state": "scheduled",
        "preset": "deploy",
        "key": "deploy",
        "not_before": NOT_BEFORE,
        "start_deadline": "2026-11-23T06:15:00Z",
        "requester": {"name": "alice", "label": "alice@laptop"},
        "source": {"mode": "git_ref", "ref": "main", "sha": "0" * 40},
        "finished_at": None,
        **extra,
    }


class Sched:
    def __init__(self, fake: FakeRcm):
        self.fake = fake
        self.health: dict[str, Any] = {"ok": True, "version": __version__, "schedule": True}
        self.docs: list[dict[str, Any]] = [scheduled_doc()]
        self.status_doc: dict[str, Any] = {
            "presets": [
                {"name": "deploy", "source_modes": ["git_ref"], "inputs": [], "pool": "default"},
                {"name": "ok", "source_modes": ["tree"], "inputs": [], "pool": "default"},
            ],
            "display_timezone": "Asia/Seoul",
            "pools": [
                {"name": "default", "queue": [], "recent": [], "scheduled": [scheduled_doc()]}
            ],
        }
        fake.route("GET", "/api/health", lambda _b: self.health)
        fake.route("GET", "/api/whoami", {"name": "alice", "admin": False})
        fake.route("GET", "/api/status", lambda _b: self.status_doc)
        fake.route(
            "POST",
            "/jobs",
            lambda _b: {
                "job_id": JOB,
                "state": "scheduled",
                "joined": False,
                "url": f"{fake.url}/#/jobs/{JOB}",
                "not_before": NOT_BEFORE,
                "start_deadline": "2026-11-23T06:15:00Z",
                "timezone": "Asia/Seoul",
            },
            status=201,
        )
        fake.route("GET", f"/jobs/{JOB}", lambda _b: self.next_doc())

    def next_doc(self) -> dict[str, Any]:
        return self.docs.pop(0) if len(self.docs) > 1 else self.docs[0]

    def submits(self) -> list[Any]:
        return [r.body for r in self.fake.calls("POST", "/jobs")]


@pytest.fixture
def srv(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("XDG_CONFIG_HOME", "RCM_LABEL", "RCM_CONFIG"):
        monkeypatch.delenv(var, raising=False)
    fake = FakeRcm()
    monkeypatch.setenv("RCM_SERVER", fake.url)
    monkeypatch.setenv("RCM_TOKEN", "alice-token")
    yield Sched(fake)
    fake.close()


# ── 파서 · 길이 ─────────────────────────────────────────────────────────────


def test_at_and_in_parse_and_exclude_each_other(capsys):
    a = build_parser().parse_args(["run", "deploy", "--at", "15:00"])
    assert a.at == "15:00" and a.start_in is None
    b = build_parser().parse_args(["run", "deploy", "--in", "5m"])
    assert b.start_in == "5m" and b.at is None
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", "deploy", "--at", "15:00", "--in", "5m"])


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("90s", 90), ("5m", 300), ("2h", 7200), ("1h30m", 5400), ("1d", 86400), ("45", 45)],
)
def test_durations(text, seconds):
    assert parse_duration(text) == seconds


@pytest.mark.parametrize("text", ["", "m", "5x", "-5m", "0", "0m", "1.5h", "5 m"])
def test_bad_durations(text):
    with pytest.raises(ValueError):
        parse_duration(text)


def test_bad_in_is_a_usage_error_before_any_request(srv, capsys):
    code, _out, err = run(capsys, ["run", "deploy", "--ref", "main", "--in", "soon"])
    assert code == 2, err
    assert "--in" in err
    assert srv.submits() == []


# ── 옛 서버 (S11) ───────────────────────────────────────────────────────────


def test_s11_a_server_without_the_schedule_key_is_refused_exit_2(srv, capsys, monkeypatch):
    srv.health.pop("schedule")
    sent: list[Any] = []
    monkeypatch.setattr(Client, "submit", lambda self, *a, **k: sent.append(a))
    code, out, err = run(capsys, ["run", "deploy", "--ref", "main", "--at", "15:00"])
    assert code == 2, (out, err)
    assert "server does not support scheduling — upgrade it" in err
    assert sent == []


def test_s11_the_tree_path_refuses_before_the_snapshot(srv, capsys, monkeypatch, tmp_path):
    srv.health.pop("schedule")

    def no_snapshot(*_a, **_k):
        raise AssertionError("no snapshot before the server said it can schedule")

    monkeypatch.setattr(cli_mod, "make_snapshot", no_snapshot)
    (tmp_path / "t").mkdir()
    code, _out, err = run(capsys, ["run", "ok", "--in", "5m", "--dir", str(tmp_path / "t")])
    assert code == 2, err
    assert "server does not support scheduling" in err


# ── 제출 본문 ───────────────────────────────────────────────────────────────


def test_at_is_sent_as_written(srv, capsys):
    code, out, err = run(capsys, ["run", "deploy", "--ref", "main", "--at", "15:00", "--no-wait"])
    assert code == 0, err
    body = srv.submits()[0]
    assert body["start_at"] == "15:00" and "start_in_seconds" not in body
    assert last_json(out)["state"] == "scheduled"


def test_in_is_sent_as_seconds(srv, capsys):
    code, _out, err = run(capsys, ["run", "deploy", "--ref", "main", "--in", "5m", "--no-wait"])
    assert code == 0, err
    body = srv.submits()[0]
    assert body["start_in_seconds"] == 300 and "start_at" not in body


def test_an_ordinary_run_sends_neither_key(srv, capsys):
    srv.fake.route("POST", "/jobs", {"job_id": JOB, "state": "queued", "joined": False}, status=201)
    srv.docs = [{**scheduled_doc(), "state": "queued", "not_before": None}]
    code, _out, err = run(capsys, ["run", "deploy", "--ref", "main", "--no-wait"])
    assert code == 0, err
    body = srv.submits()[0]
    assert "start_at" not in body and "start_in_seconds" not in body


def test_no_wait_says_when_it_will_run(srv, capsys):
    code, _out, err = run(capsys, ["run", "deploy", "--ref", "main", "--at", "15:00", "--no-wait"])
    assert code == 0, err
    assert "scheduled for" in err


# ── wait · jobs · eta ───────────────────────────────────────────────────────


def test_wait_on_a_schedule_says_scheduled_for_and_keeps_waiting(srv, capsys):
    srv.docs = [scheduled_doc(), {**scheduled_doc(), "state": "succeeded", "exit_code": 0}]
    code, _out, err = run(capsys, ["wait", "--job", str(JOB), "--poll"])
    assert code == 0, err
    assert "scheduled for" in err


def test_wait_on_an_expired_job_exits_2(srv, capsys):
    srv.docs = [{**scheduled_doc(), "state": "expired", "summary": "did not start"}]
    code, out, err = run(capsys, ["wait", "--job", str(JOB), "--poll"])
    assert code == 2, err
    assert last_json(out)["wait_exit_code"] == 2


def test_jobs_lists_schedules_and_filters_them(srv, capsys):
    code, out, err = run(capsys, ["jobs", "--state", "scheduled", "--json"])
    assert code == 0, err
    rows = json.loads(out)
    assert [r["id"] for r in rows] == [JOB]
    code, out, _err = run(capsys, ["jobs"])
    assert code == 0
    line = next(ln for ln in out.splitlines() if ln.startswith(f"#{JOB}"))
    assert "scheduled" in line and "at " in line


def test_eta_on_a_schedule_does_not_invent_a_number(srv, capsys):
    code, out, err = run(capsys, ["eta", "--job", str(JOB)])
    assert code == 0, err
    assert "scheduled for" in out
    assert "eta" not in out.replace("scheduled for", "")


# ── 확실한 409 거절은 종료 2 (H 실기 · #257) ─────────────────────────────────
#
# 409 는 상태 코드만으로는 잡이 생겼는지 모른다(3). 그러나 `schedule_exists` · `golive_running` ·
# `release_route_required` 는 서버가 잡을 만들지 않았다고 확실히 말한 것이다 — 2 다.


class _Snap:
    """tree 경로의 제출까지만 가는 가짜 스냅샷(업로드 전에 409 를 받는다)."""

    repo = "org/app"
    branch = "feat/x"
    base_sha = "0" * 40
    dirty = False
    tree_hash = "ab" * 32
    bytes = 10

    def __init__(self, tar_path) -> None:
        self.tar_path = tar_path


REFUSED_409 = ("schedule_exists", "golive_running", "release_route_required")


@pytest.mark.parametrize("code", REFUSED_409)
def test_a_known_409_refusal_on_the_git_ref_path_exits_2(srv, capsys, code):
    srv.fake.route(
        "POST",
        "/jobs",
        {"error": f"refused: {code}", "code": code, "error_code": code, "job_id": 12},
        status=409,
    )
    rc, _out, err = run(capsys, ["run", "deploy", "--ref", "main", "--at", "15:00", "--no-wait"])
    assert rc == 2, err
    assert "submit failed" in err


@pytest.mark.parametrize("code", REFUSED_409)
def test_a_known_409_refusal_on_the_tree_path_exits_2(srv, capsys, monkeypatch, tmp_path, code):
    monkeypatch.setattr(cli_mod, "make_snapshot", lambda *_a, **_k: _Snap(tmp_path / "snap.tar"))
    srv.fake.route(
        "POST",
        "/jobs",
        {"error": f"refused: {code}", "code": code, "error_code": code, "job_id": 12},
        status=409,
    )
    (tmp_path / "t").mkdir()
    rc, _out, err = run(capsys, ["run", "ok", "--in", "5m", "--dir", str(tmp_path / "t")])
    assert rc == 2, err


def test_an_unknown_409_still_exits_3(srv, capsys):
    """모르는 409 는 여전히 「결과 모름」이다 — 아는 코드만 확실한 거절로 친다."""
    srv.fake.route("POST", "/jobs", {"error": "conflict", "code": "something_else"}, status=409)
    rc, _out, err = run(capsys, ["run", "deploy", "--ref", "main", "--no-wait"])
    assert rc == 3, err
