"""M5s D — CLI 의 반복 사슬: `rcm wait` 가 `retried_as` 를 따라간다 · 최종 JSON 의 `chain_id` ·
`attempts[]` · 사슬 전체에 거는 `--timeout` · 마지막 시도에서 받는 산출물 · `rcm cancel` 의 뿌리
비밀 · 스킬 템플릿(`docs/m5s-workplan.md` §4.2 · 결정 211 · 228).

서버는 `fake_rcm_http.FakeRcm` 이다 — 잡 문서를 손으로 써서 CLI 가 무엇을 하는지만 잰다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from fake_rcm_http import FakeRcm
from remote_ci_monitor import __version__, cli, submissions
from remote_ci_monitor.client import Client, wait_for_chain
from test_cli_m1 import last_json


def run(capsys, argv: list[str]) -> tuple[int, str, str]:
    try:
        code = cli.main(argv)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 2
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def doc(jid: int, state: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": jid,
        "state": state,
        "preset": "golive",
        "key": "golive",
        "requester": {"name": "alice", "label": "alice@laptop"},
        "source": {"mode": "git_ref", "ref": "main", "sha": "0" * 40},
        "chain_id": 57,
        "max_attempts": 3,
        **extra,
    }


@pytest.fixture
def fake(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for var in ("XDG_CONFIG_HOME", "RCM_LABEL", "RCM_CONFIG"):
        monkeypatch.delenv(var, raising=False)
    f = FakeRcm()
    monkeypatch.setenv("RCM_SERVER", f.url)
    monkeypatch.setenv("RCM_TOKEN", "alice-token")
    f.route("GET", "/api/health", {"ok": True, "version": __version__})
    yield f
    f.close()


def chain(fake: FakeRcm, last: dict[str, Any]) -> None:
    fake.route(
        "GET",
        "/jobs/57",
        doc(57, "retried", attempt=1, exit_code=75, retried_as=58, summary="login needed"),
    )
    fake.route(
        "GET", "/jobs/58", doc(58, "retried", attempt=2, exit_code=75, retried_as=59, retry_of=57)
    )
    fake.route("GET", "/jobs/59", {**doc(59, "scheduled", attempt=3, retry_of=58), **last})


def test_wait_follows_retried_as_to_the_last_attempt(fake, capsys):
    chain(fake, {"state": "succeeded", "exit_code": 0})
    code, out, err = run(capsys, ["wait", "--job", "57", "--poll"])
    assert code == 0, err
    body = last_json(out)
    assert body["job_id"] == 59 and body["chain_id"] == 57
    assert body["attempts"] == [
        {"job_id": 57, "attempt": 1, "state": "retried", "exit_code": 75},
        {"job_id": 58, "attempt": 2, "state": "retried", "exit_code": 75},
        {"job_id": 59, "attempt": 3, "state": "succeeded", "exit_code": 0},
    ]
    assert "#57 exit 75 · attempt 1 of 3 · retrying as #58" in err
    assert "#58 exit 75 · attempt 2 of 3 · retrying as #59" in err


def test_an_exhausted_chain_exits_1(fake, capsys):
    chain(fake, {"state": "exhausted", "exit_code": 75, "summary": "exit 75 on all 3 attempts"})
    code, out, err = run(capsys, ["wait", "--job", "57", "--poll"])
    assert code == 1, err
    assert last_json(out)["state"] == "exhausted" and last_json(out)["wait_exit_code"] == 1


def test_waiting_on_a_middle_attempt_still_lists_the_earlier_ones(fake, capsys):
    chain(fake, {"state": "succeeded", "exit_code": 0})
    code, out, _err = run(capsys, ["wait", "--job", "58", "--poll"])
    assert code == 0
    assert [a["job_id"] for a in last_json(out)["attempts"]] == [57, 58, 59]


def test_a_retried_attempt_without_a_next_id_is_unknown_exit_3(fake, capsys):
    """결정 225 — 서버가 지키는 불변식이 깨진 사슬은 「모른다」(3)다. 실패로 말하지 않는다."""
    fake.route("GET", "/jobs/57", doc(57, "retried", attempt=1, exit_code=75, retried_as=None))
    code, out, err = run(capsys, ["wait", "--job", "57", "--poll"])
    assert code == 3, err
    assert "names no next attempt" in err
    assert last_json(out)["wait_exit_code"] == 3


def test_a_job_without_a_chain_has_no_chain_keys(fake, capsys):
    fake.route(
        "GET",
        "/jobs/9",
        {**doc(9, "succeeded", exit_code=0), "chain_id": None, "max_attempts": None},
    )
    code, out, _err = run(capsys, ["wait", "--job", "9", "--poll"])
    assert code == 0
    body = last_json(out)
    assert "attempts" not in body and body["chain_id"] is None


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def test_the_timeout_covers_the_whole_chain():
    """`--timeout` 은 사슬 전체다 — 둘째 시도는 첫 시도가 쓰고 남은 시간만 받는다."""
    clock = _Clock()
    given: list[float | None] = []
    docs = {
        1: doc(1, "retried", exit_code=75, retried_as=2),
        2: doc(2, "succeeded", exit_code=0),
    }

    def fake_wait(client, job_id, *, timeout=None, clock=None, **kw):
        given.append(timeout)
        clock_obj.t += 40  # 이 시도를 기다리는 데 40초
        return (0 if job_id == 2 else 3), docs[job_id], None

    clock_obj = clock
    code, job, reason, attempts = wait_for_chain(
        Client("http://127.0.0.1:9", "t"), 1, timeout=100, clock=clock, wait=fake_wait
    )
    assert code == 0 and job["id"] == 2 and reason is None
    assert given == [100, 60]
    assert [a["job_id"] for a in attempts] == [1, 2]


def test_fetch_artifacts_comes_from_the_last_attempt(fake, monkeypatch, tmp_path):
    chain(fake, {"state": "succeeded", "exit_code": 0})
    fetched: list[int] = []

    def fake_fetch(client, job_id, root, **kw):
        fetched.append(job_id)
        return {"ok": True}

    monkeypatch.setattr(cli, "_fetch_artifacts", fake_fetch)
    client = Client(fake.url, "alice-token")
    spec = cli._FetchSpec(root=tmp_path / "out", baseline={})
    code = cli._wait(client, 57, timeout=None, joined=False, use_sse=False, fetch=spec)
    assert code == 0 and fetched == [59]


def test_cancel_by_a_later_attempt_finds_the_roots_cancel_token(fake, capsys):
    """비밀은 뿌리 잡을 낼 때 받았다(결정 228) — 뒤 시도 id 로 불러도 상태 파일에서 찾는다."""
    secret = "tok-" + "x" * 20
    submissions.remember(
        fake.url,
        57,
        "s57",
        secret,
        role=submissions.ROLE_CANCEL_JOB,
        token_fingerprint=submissions.fingerprint("alice-token"),
        finished=lambda *_: None,
    )
    fake.route("GET", "/jobs/59", doc(59, "scheduled", attempt=3))
    fake.route("POST", "/jobs/59/cancel", {"job_id": 59, "state": "cancelled"})
    code, _out, err = run(capsys, ["cancel", "59"])
    assert code == 0, err
    sent = [r.body for r in fake.calls("POST", "/jobs/59/cancel")]
    assert sent and sent[-1].get("cancel_token") == secret


def test_the_state_file_keeps_a_secret_whose_root_was_only_retried(fake, monkeypatch):
    """`retried` 는 끝이 아니다 — 상태 파일 정리가 사슬의 비밀을 「끝난 잡」으로 버리면 머리를
    멈출 비밀이 사라진다. 정리가 묻는 함수(`finished`)가 그 잡을 끝나지 않은 것으로 답한다."""
    fake.route("GET", "/jobs/57", doc(57, "retried", exit_code=75, retried_as=58))
    fake.route("GET", "/jobs/60", doc(60, "succeeded", exit_code=0))
    seen: dict[str, Any] = {}
    monkeypatch.setattr(submissions, "remember", lambda *a, **kw: seen.update(kw))
    client = Client(fake.url, "alice-token")
    resp = {"submission": {"id": "s61", "cancel_token": "tok-" + "y" * 20}, "joined": False}
    cli._keep_submission(client, 61, resp)
    finished = seen["finished"]
    assert finished(fake.url, 57) is False
    assert finished(fake.url, 60) is True


# ── 스킬 템플릿 ─────────────────────────────────────────────────────────────


def test_the_ci_watcher_waits_through_retried_and_ends_on_exhausted():
    from ci_fakes import load_templates

    cfgmod, _wait, watchmod, _status = load_templates()
    assert "exhausted" in watchmod.TERMINAL and "retried" not in watchmod.TERMINAL
    assert (
        cfgmod.terminal_status("retried", 75, None, job=1, summary="", step=None, successor=None)
        is None
    )
    got = cfgmod.terminal_status(
        "exhausted", 75, None, job=1, summary="", step=None, successor=None
    )
    assert got is not None and got[0] == "error" and "unavailable" in got[1]


def test_the_release_check_maps_exhausted_and_retried_to_unknown():
    import importlib.util

    path = Path(cli.__file__).parent / "skills/rcm-release-driver/templates/release_check.py"
    spec = importlib.util.spec_from_file_location("release_check_m5s", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.map_exit({"state": "exhausted", "exit_code": 75})[0] == 3
    assert mod.map_exit({"state": "retried", "exit_code": 75})[0] == 3
