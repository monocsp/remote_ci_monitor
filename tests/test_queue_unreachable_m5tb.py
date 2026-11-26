"""M5t-b PR3 — 끊김 유예 중인 잡이 사람에게 어떻게 보이나(docs/m5tb-workplan.md §4.8 · 결정 276).

원격 워커와 연락이 끊겨 레인은 `down` 인데 잡은 유예(G) 동안 `running` 으로 남는다(PR1). 그 잡의
사유는 새 값 `worker_unreachable_grace` 이고, `estimate.finish_at` 은 null(`—`)이며, 「확인이
필요한 작업」에서는 `worker_down` 바로 뒤에 선다. 사람에게는 원문 코드가 아니라 「워커와 연락이
끊김 — 유예 안에 돌아오면 이어 감」 꼴의 문장으로 보인다(`rcm top` · `rcm wait` stderr · 웹).

`log_complete = false` 와 `late_result` 의 사람용 줄(RL20 · §4.6)도 여기서 잠근다. 두 키는 PR2 가
만든다 — 키가 없는 문서(옛 서버 · PR2 전)에는 아무 줄도 더하지 않는다.

시나리오: RL28 · RL20(PR3 몫 — `rcm logs` 줄) · RL29(PR3 몫 — 사람용 마지막 줄).
"""

from __future__ import annotations

import functools
from dataclasses import replace
from datetime import UTC
from typing import Any

import pytest

from fake_rcm_http import FakeRcm, RawReply
from jobfactory import CFG, NOW, PRESETS, ago, job
from remote_ci_monitor import __version__
from remote_ci_monitor import cli as cli_mod
from remote_ci_monitor import client as client_mod
from remote_ci_monitor.cli import main
from remote_ci_monitor.core import model
from remote_ci_monitor.core.model import (
    CANCELLING,
    RUNNING,
    CancelInfo,
    Progress,
    Step,
    WorkerInfo,
)
from remote_ci_monitor.core.queue import compute_queue, eta_for_new
from remote_ci_monitor.core.render_text import render
from test_cli_m1 import last_json

GRACE = "worker_unreachable_grace"


def remote_lane(name: str, state: str, n: int = 1, job_id: int | None = None) -> WorkerInfo:
    return WorkerInfo(
        lane=n, state=state, job_id=job_id, worker=name, since=ago(minutes=3), host_key="h-" + name
    )


def busy(job_id: int, *, worker: str | None = "mac2", state: str = RUNNING, **kw: Any):
    return job(job_id, state=state, started_min=4, lane=1, worker_name=worker, **kw)


def rows(jobs, workers):
    return {
        r.job.id: r
        for r in compute_queue(
            jobs, workers=workers, paused=False, medians={}, presets=PRESETS, cfg=CFG, now=NOW
        )
    }


# ── 사유 (compute_queue) ─────────────────────────────────────────────────────


def test_rl28_a_running_job_on_a_down_worker_is_worker_unreachable_grace_without_an_eta():
    r = rows([busy(901)], [remote_lane("mac2", "down")])[901]
    assert r.job.state == RUNNING  # 잡은 유예 동안 그대로 돈다(PR1)
    assert r.reason == GRACE
    assert r.estimate.finish_at is None  # 끝날 시각을 지어내지 않는다


def test_rl28_the_same_job_on_a_live_worker_is_running_with_an_eta():
    r = rows([busy(902)], [remote_lane("mac2", "busy", job_id=902)])[902]
    assert r.reason == "running"
    assert r.estimate.finish_at is not None


def test_rl28_unreachable_comes_before_cancelling_stuck_and_needs_action():
    """§4.8: `_busy_reason` 의 맨 앞이다. 취소 중이어도 그렇다 — 취소가 닿지 못한다는 사실이
    더 행동에 쓸모 있다."""
    jobs = [
        busy(903, state=CANCELLING, cancel=CancelInfo(ago(minutes=1), "alice", None)),
        busy(904, last_output_at=ago(minutes=200)),  # 아주 오래 조용하다 — 살아 있으면 stuck
    ]
    got = rows(jobs, [remote_lane("mac2", "down", 1), remote_lane("mac2", "down", 2)])
    assert got[903].reason == GRACE
    assert got[904].reason == GRACE
    assert got[903].estimate.finish_at is None and got[904].estimate.finish_at is None


def test_rl28_a_local_lane_job_is_never_unreachable():
    """로컬 레인 잡은 `worker_name` 이 없다 — 원격 워커 하나가 down 이어도 그 잡과 무관하다."""
    got = rows([busy(905, worker=None)], [remote_lane("mac2", "down")])
    assert got[905].reason == "running"


def test_rl28_another_workers_down_lane_does_not_touch_this_job():
    got = rows([busy(906, worker="build-02")], [remote_lane("mac2", "down")])
    assert got[906].reason == "running"


def test_rl28_a_job_waiting_behind_an_unreachable_job_in_its_group_has_no_eta():
    """# RL28 끊긴 잡의 끝은 모른다 — 그 그룹 뒤에서 기다리는 잡의 `wait` · `finish` ·
    `blocked_by.remaining_seconds` 도 그 모르는 끝에서 숫자를 지어내지 않는다(PLAN 「fail-open
    금지」). 같은 그룹의 다음 대기 잡도 그렇다. 살아 있는 레인은 그 대기 잡이 차지하지 않는다 —
    그룹이 풀리기 전에는 claim 되지 않으므로, 다른 그룹의 잡은 그 레인의 ETA 를 그대로 갖는다."""
    lanes = [remote_lane("mac2", "down"), remote_lane("build-02", "idle")]
    jobs = [
        busy(910, key="qa", group="devices"),
        job(911, key="qa", group="devices"),
        job(912, key="qa", group="devices"),
        job(913, key="deploy-dev"),
    ]
    got = rows(jobs, lanes)
    assert got[910].reason == GRACE
    for waiter in (911, 912):
        w = got[waiter]
        assert w.reason == "blocked_by_group", w
        assert w.blocked_by is not None and w.blocked_by.job_id == 910
        assert w.blocked_by.remaining_seconds is None, w.blocked_by
        assert w.estimate.wait_seconds is None, w.estimate
        assert w.estimate.finish_at is None, w.estimate
    other = got[913]
    assert other.estimate.wait_seconds == 0 and other.estimate.finish_at is not None


def test_rl28_a_waiter_behind_an_unreachable_job_leaves_its_lane_to_the_next_job():
    """# RL28 P3 r2 — 끊긴 잡 뒤의 그룹 대기 잡은 레인을 차지하지 않는다: 같은 레인의 다음 잡은
    그 대기 잡을 세지 않고 지금 시작한다고 본다(`wait` 0, 앞 잡 없음). 워커가 돌아오면 늦어질 수
    있는 가정이다(문서 「When a worker cannot be reached」). 대조로 살아 있는 잡 뒤의 대기 잡은
    그 레인을 차지해 다음 잡이 그 뒤로 밀린다. 골든과 무관하게 이 결정만 잠근다."""
    jobs = [
        busy(920, key="qa", group="devices"),
        job(921, key="qa", group="devices"),
        job(922, key="deploy-dev"),
    ]
    down = rows(jobs, [remote_lane("mac2", "down"), remote_lane("build-02", "idle")])
    assert down[922].estimate.wait_seconds == 0, down[922].estimate
    assert down[922].ahead_job_id is None, down[922]
    assert down[921].reason == "blocked_by_group" and down[921].estimate.wait_seconds is None
    live = rows(jobs, [remote_lane("mac2", "busy", job_id=920), remote_lane("build-02", "idle")])
    assert live[921].estimate.wait_seconds is not None
    assert live[922].estimate.wait_seconds > 0, live[922].estimate  # 두 레인 다 앞이 있다


def test_rl28_the_same_waiter_behind_a_live_job_keeps_its_eta():
    """대조: 홀드 잡의 워커가 살아 있으면 오늘처럼 숫자다."""
    lanes = [remote_lane("mac2", "busy", job_id=910), remote_lane("build-02", "idle")]
    got = rows([busy(910, key="qa", group="devices"), job(911, key="qa", group="devices")], lanes)
    w = got[911]
    assert w.reason == "blocked_by_group"
    assert w.blocked_by is not None and w.blocked_by.remaining_seconds is not None
    assert w.estimate.wait_seconds is not None and w.estimate.finish_at is not None


def test_rl28_the_reason_is_a_wire_value_right_after_worker_down():
    assert model.REASON_WORKER_UNREACHABLE_GRACE == GRACE
    order = model.ACTIONABLE_REASONS
    assert order.index(GRACE) == order.index("worker_down") + 1


# ── rcm top ──────────────────────────────────────────────────────────────────


def top_row(**over: Any) -> dict[str, Any]:
    r: dict[str, Any] = {
        "id": 907,
        "pool": "default",
        "state": "running",
        "key": "ship",
        "requester": {"name": "alice", "label": "alice@mbp"},
        "joiners": [],
        "source": {"mode": "git_ref", "repo": "org/app", "ref": "main", "sha": "25e1494ab0f1"},
        "estimate": {
            "elapsed_seconds": 240.0,
            "remaining_seconds": 60.0,
            "source": "preset",
            "sample_count": 0,
            "finish_at": None,
        },
        "reason": GRACE,
        "lane": 1,
        "ran_on": "mac2",
        "placement": "pool",
        "placement_detail": [],
        "position": None,
    }
    r.update(over)
    return r


def top_text(*queue: dict[str, Any]) -> str:
    doc = {
        "schema_version": 1,
        "generated_at": "2026-11-27T09:00:00Z",
        "display_timezone": "UTC",
        "server": {"lanes": 1, "workers": []},
        "pools": [
            {"name": "default", "queue": list(queue), "recent": [], "medians": {}, "hosts": []}
        ],
    }
    return render(doc, tz=UTC)


def row_block(text: str, job_id: int) -> list[str]:
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if f"#{job_id} " in ln:
            out = [ln]
            for nxt in lines[i + 1 :]:
                if not nxt.startswith("        "):
                    break
                out.append(nxt)
            return out
    raise AssertionError(f"no row for #{job_id}:\n{text}")


def test_rl28_rcm_top_says_lost_contact_in_words_and_never_the_code():
    head, reason, *_ = row_block(top_text(top_row()), 907)
    assert reason.strip() == (
        "lost contact with mac2 — the job goes on if the worker is back within the grace period"
    )
    assert GRACE not in top_text(top_row())
    assert "eta —" in head


def test_rl28_rcm_top_without_a_machine_name_says_the_worker():
    _head, reason, *_ = row_block(top_text(top_row(ran_on=None)), 907)
    assert reason.strip().startswith("lost contact with the worker — ")


# ── rcm wait ─────────────────────────────────────────────────────────────────

JOB = 907


class Script:
    """잡 문서를 차례대로 내주는 가짜 서버(마지막 것은 계속)."""

    def __init__(self, fake: FakeRcm):
        self.fake = fake
        self.docs: list[dict[str, Any]] = []
        fake.route("GET", "/api/health", {"ok": True, "version": __version__})
        fake.route("GET", f"/jobs/{JOB}", lambda _b: self.next_doc())
        log = RawReply(b"step 1\n", {"X-RCM-Next-Offset": "7", "X-RCM-More": "0"})
        fake.route("GET", f"/jobs/{JOB}/log", log)

    def next_doc(self) -> dict[str, Any]:
        return self.docs.pop(0) if len(self.docs) > 1 else self.docs[0]


def doc(state: str = "running", reason: str | None = "running", **extra: Any) -> dict[str, Any]:
    d: dict[str, Any] = {
        "id": JOB,
        "state": state,
        "preset": "ship",
        "exit_code": None if state == "running" else (0 if state == "succeeded" else 1),
        "url": f"http://build.example:8787/#/jobs/{JOB}",
        "keep": False,
        "shelf": None,
        "reason": reason if state == "running" else None,
        "ran_on": "mac2",
        "action": None,
    }
    d.update(extra)
    return d


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


LOST_LINE = (
    f"#{JOB} lost contact with worker mac2 — the job goes on if the worker is back within "
    "the grace period"
)
BACK_LINE = f"#{JOB} worker mac2 is back — the job goes on"


def test_rl28_rcm_wait_says_once_that_the_worker_is_unreachable_and_keeps_waiting(fake, capsys):
    fake.docs = [doc(), doc(reason=GRACE), doc(reason=GRACE), doc(reason=GRACE), doc("succeeded")]
    code, out, err = run(capsys, ["wait", "--job", str(JOB), "--poll"])
    assert code == 0, err  # 유예 동안 끝내지 않는다 — 결과가 오면 그 결과다
    assert [ln for ln in err.splitlines() if "lost contact" in ln] == [LOST_LINE], err
    assert GRACE not in err
    assert last_json(out)["wait_exit_code"] == 0


def test_rl28_rcm_run_prints_the_same_lines_while_it_waits(fake, capsys):
    """# RL28 P3 r1: `rcm run` 도 같은 `_wait` 로 기다린다 — 끊김 · 돌아옴 줄을 `rcm wait` 와
    똑같이 한 번씩 찍는다(문서는 「rcm wait 과 rcm run 이 기다리는 동안」)."""
    fake.fake.route("GET", "/api/whoami", {"name": "alice", "admin": False})
    fake.fake.route(
        "GET",
        "/api/status",
        {"presets": [{"name": "ship", "source_modes": ["git_ref"], "inputs": [], "keep": False}]},
    )
    fake.fake.route(
        "POST",
        "/jobs",
        {"job_id": JOB, "state": "queued", "joined": False, "url": f"{fake.fake.url}/#/jobs/{JOB}"},
    )
    fake.docs = [doc(), doc(reason=GRACE), doc(reason=GRACE), doc(), doc("succeeded")]
    code, _out, err = run(capsys, ["run", "ship", "--ref", "main", "--poll"])
    assert code == 0, err
    assert [ln for ln in err.splitlines() if "lost contact" in ln or "is back" in ln] == [
        LOST_LINE,
        BACK_LINE,
    ], err
    assert GRACE not in err


def test_rl28_rcm_wait_says_when_the_worker_is_back(fake, capsys):
    fake.docs = [doc(reason=GRACE), doc(reason=GRACE), doc(), doc(), doc("succeeded")]
    code, _out, err = run(capsys, ["wait", "--job", str(JOB), "--poll"])
    assert code == 0, err
    assert [ln for ln in err.splitlines() if "lost contact" in ln or "is back" in ln] == [
        LOST_LINE,
        BACK_LINE,
    ], err


def test_rl28_rcm_wait_with_a_timeout_during_the_grace_ends_3(fake, capsys):
    fake.docs = [doc(reason=GRACE)]
    code, _out, err = run(capsys, ["wait", "--job", str(JOB), "--poll", "--timeout", "1"])
    assert code == 3, err
    assert LOST_LINE in err


# ── log_complete · late_result 의 사람용 줄 (RL20 · §4.6, 키는 PR2) ──────────────

INCOMPLETE = (
    f"#{JOB} log incomplete — the worker could not keep all of it; rcm logs {JOB} shows "
    "what arrived"
)


def test_rl20_rcm_wait_says_the_log_is_incomplete_when_the_job_says_false(fake, capsys):
    fake.docs = [doc(), doc("failed", log_complete=False)]
    code, _out, err = run(capsys, ["wait", "--job", str(JOB), "--poll"])
    assert code == 1, err
    assert INCOMPLETE in err.splitlines(), err


@pytest.mark.parametrize("value", [True, None, ...])
def test_rl20_rcm_wait_says_nothing_when_the_log_is_whole_unknown_or_the_key_is_missing(
    fake, capsys, value
):
    extra = {} if value is ... else {"log_complete": value}
    fake.docs = [doc(), doc("succeeded", **extra)]
    code, _out, err = run(capsys, ["wait", "--job", str(JOB), "--poll"])
    assert code == 0, err
    assert "log incomplete" not in err


LATE = {
    "outcome": "succeeded",
    "exit_code": 0,
    "finished_at": "2026-11-27T09:10:00Z",
    "received_at": "2026-11-27T09:12:00Z",
    "log_complete": True,
}


def test_rl29_rcm_wait_shows_a_late_result_as_one_line_that_does_not_count(fake, capsys):
    lost = doc(
        "lost",
        exit_code=None,
        summary_code="worker_unreachable",
        summary_args={"name": "mac2", "seconds": 660},
        late_result=LATE,
    )
    fake.docs = [doc(), lost]
    code, out, err = run(capsys, ["wait", "--job", str(JOB), "--poll"])
    assert code == 3, err  # 늦은 결과는 잡의 결과가 아니다 — 종료 코드는 lost 의 3
    want = f"#{JOB} late result: succeeded (exit 0) — not counted as this job's result"
    assert [ln for ln in err.splitlines() if "late result" in ln] == [want], err
    assert last_json(out)["late_result"] == LATE


@pytest.mark.parametrize("value", [None, ..., "garbage", {"outcome": "\x1b[2Jsucceeded"}])
def test_rl29_rcm_wait_has_no_late_line_without_a_readable_late_result(fake, capsys, value):
    extra = {} if value is ... else {"late_result": value}
    fake.docs = [doc(), doc("lost", exit_code=None, summary_code="worker_unreachable", **extra)]
    code, _out, err = run(capsys, ["wait", "--job", str(JOB), "--poll"])
    assert code == 3, err
    assert "late result" not in err


def test_rl20_rcm_logs_ends_with_a_line_on_stderr_when_the_log_is_incomplete(fake, capsys):
    fake.docs = [doc("failed", log_complete=False)]
    code, _out, err = run(capsys, ["logs", str(JOB)])
    assert code == 0, err
    assert err.splitlines()[-1:] == [INCOMPLETE], err


@pytest.mark.parametrize("value", [True, None, ...])
def test_rl20_rcm_logs_adds_nothing_otherwise(fake, capsys, value):
    extra = {} if value is ... else {"log_complete": value}
    fake.docs = [doc("succeeded", **extra)]
    code, _out, err = run(capsys, ["logs", str(JOB)])
    assert code == 0, err
    assert "log incomplete" not in err


def test_rl20_rcm_logs_still_prints_the_log_when_the_job_document_cannot_be_read(fake, capsys):
    fake.fake.route("GET", f"/jobs/{JOB}", {"error": "boom"}, status=500)
    code, _out, err = run(capsys, ["logs", str(JOB)])
    assert code == 0, err
    assert "log incomplete" not in err


# ── 연락 두절의 다섯 끝 (RL29 · 사람용 마지막 줄) ─────────────────────────────

FIVE = [
    ("worker_unreachable", {"name": "mac2", "seconds": 660}, "worker mac2 unreachable for 660s"),
    (
        "worker_self_stopped",
        {"seconds": 575},
        "worker stopped the job itself after 575s without the server",
    ),
    ("worker_replaced", {"name": "mac2"}, "worker mac2 was replaced by a new process"),
    ("worker_restarted_without_job", {"name": "mac2"}, "worker mac2 restarted without the job"),
    (
        "orphan_unverified",
        {"name": "mac2", "seconds": 660},
        "the job may still be running on that machine, check it",
    ),
]


@pytest.mark.parametrize(("code", "args", "want"), FIVE, ids=[f[0] for f in FIVE])
def test_rl29_each_lost_ending_waits_3_with_its_own_sentence_and_the_log_path(
    fake, capsys, code, args, want
):
    lost = doc(
        "lost",
        exit_code=None,
        summary=None,
        summary_code=code,
        summary_args=args,
        failed_step=None,
        last_step=None,
    )
    fake.docs = [doc(), lost]
    rc, out, err = run(capsys, ["wait", "--job", str(JOB), "--poll"])
    assert rc == 3, err
    assert want in err, err
    assert f"rcm logs {JOB}" in err, err
    assert code not in err  # 원문 코드는 보이지 않는다
    assert last_json(out)["summary_code"] == code


# ── P6-1 G4: rcm eta --job N 이 새 사유를 말한다 ─────────────────────────────────


def eta_job(fake: Script, capsys, queue: list[dict[str, Any]], job_id: int) -> str:
    fake.fake.route(
        "GET",
        "/api/status",
        {"schema_version": 1, "pools": [{"name": "default", "queue": queue, "recent": []}]},
    )
    code, out, err = run(capsys, ["eta", "--job", str(job_id)])
    assert code == 0, err
    return out.strip()


def waiter_row(job_id: int, **over: Any) -> dict[str, Any]:
    r = top_row(
        id=job_id,
        state="queued",
        position=1,
        lane=None,
        ran_on=None,
        reason="blocked_by_group",
        blocked_by={"job_id": 907, "group": "devices", "remaining_seconds": None},
        estimate={"waited_seconds": 30.0, "wait_seconds": None, "finish_at": None},
    )
    r.update(over)
    return r


def test_g4_rl28_rcm_eta_job_says_lost_contact_for_a_job_in_the_grace(fake, capsys):
    """# RL28 P6-1 G4: 끊김 유예 중인 도는 잡은 「running · elapsed … · eta —」만 찍지 않고
    `rcm top` 과 같은 사람 문장을 붙인다. 원문 코드는 없다."""
    out = eta_job(fake, capsys, [top_row()], 907)
    assert out.startswith("#907 · running · elapsed 4m"), out
    assert (
        "eta — · lost contact with mac2 — the job goes on if the worker is back within the "
        "grace period" in out
    ), out
    assert GRACE not in out


def test_g4_rl28_rcm_eta_job_says_why_a_waiter_behind_an_unreachable_job_has_no_eta(fake, capsys):
    out = eta_job(fake, capsys, [top_row(), waiter_row(911)], 911)
    assert "wait — " in out and "eta —" in out, out
    assert "blocked by #907 · devices · frees in —" in out, out


def test_g4_rl45_rcm_eta_job_of_a_held_waiter_says_held_and_does_not_count_ahead(fake, capsys):
    """# RL45 P6-1 G4: 홀드에 막힌 대기 잡은 오지 않을 순번(`N ahead`)을 보이지 않는다 —
    `no_capable_machine` 처럼 앞선 잡 수를 모른다. 사유는 `rcm top` 과 같은 말이다."""
    held = waiter_row(
        921,
        position=2,
        reason="needs_action",
        blocked_by={"job_id": 812, "group": "irreversible:ship", "remaining_seconds": None},
        action={"code": "irreversible_hold", "note": "x", "at": "2026-11-27T08:50:00Z"},
    )
    out = eta_job(fake, capsys, [top_row(), waiter_row(911), held], 921)
    assert "ahead" not in out, out
    assert "group irreversible:ship is held by #812 — rcm release-hold 812" in out, out
    assert "irreversible_hold" not in out, out


# ── P6-1 G5: 취소 중인 잡은 끊긴 워커가 돌아오면 이어지지 않고 취소된다 ─────────────────

CANCEL_TAIL = "the job is cancelled when the worker is back, or ends as lost after the grace period"


def test_g5_rl28_rcm_top_does_not_promise_a_cancelling_job_goes_on():
    """# RL28 P6-1 G5: 명세 결정 322 — 다시 붙은 워커는 취소 요청이 있는 잡을 멈춘다."""
    _head, reason, *_ = row_block(top_text(top_row(state="cancelling")), 907)
    assert reason.strip() == f"lost contact with mac2 — {CANCEL_TAIL}", reason
    assert "goes on" not in reason


def test_g5_rl28_rcm_wait_on_a_cancelling_job_says_it_is_cancelled_when_the_worker_is_back(
    fake, capsys
):
    cancelling = {**doc(), "state": "cancelling", "reason": GRACE}
    back = {**doc(), "state": "cancelling", "reason": "cancelling"}
    fake.docs = [cancelling, cancelling, back, doc("cancelled")]
    _code, _out, err = run(capsys, ["wait", "--job", str(JOB), "--poll"])
    said = [ln for ln in err.splitlines() if "lost contact" in ln or "is back" in ln]
    assert said == [
        f"#{JOB} lost contact with worker mac2 — {CANCEL_TAIL}",
        f"#{JOB} worker mac2 is back — the job is being cancelled",
    ], err
    assert "goes on" not in err


# ── P6-1 G6: 끊긴 동안은 살아 있다고 보이지 않는다 ─────────────────────────────────


def test_g6_rl28_an_unreachable_job_is_never_overdue_or_stuck():
    """# RL28 P6-1 G6: 끊긴 잡의 경과 · 출력 시각은 마지막으로 들은 값이다. 그 값으로 「초과」 ·
    「멈춤」을 판정하면 「lost contact」 옆에 「overdue」가 붙는다. 끊긴 동안은 내지 않는다.
    대조: 같은 잡이 살아 있는 워커에 있으면 둘 다 참이다."""
    old = job(930, state=RUNNING, started_min=500, lane=1, worker_name="mac2")
    quiet = job(
        931,
        state=RUNNING,
        started_min=4,
        lane=2,
        worker_name="mac2",
        last_output_at=ago(minutes=200),
    )
    live = rows(
        [old, quiet],
        [remote_lane("mac2", "busy", 1, job_id=930), remote_lane("mac2", "busy", 2, job_id=931)],
    )
    assert live[930].estimate.overdue and live[931].estimate.stuck, (live[930], live[931])
    down = rows([old, quiet], [remote_lane("mac2", "down", 1), remote_lane("mac2", "down", 2)])
    for jid in (930, 931):
        est = down[jid].estimate
        assert down[jid].reason == GRACE
        assert not est.overdue and not est.stuck and est.stuck_code is None, est


def test_g6_rl28_rcm_wait_progress_line_has_no_overdue_while_unreachable():
    """옛 서버가 끊긴 잡에 `overdue: true` 를 보내도 진행 줄은 「lost contact」 옆에 「overdue」를
    찍지 않는다."""
    d = {
        "id": JOB,
        "state": "running",
        "reason": GRACE,
        "estimate": {"elapsed_seconds": 900.0, "finish_at": None, "overdue": True},
    }
    assert "overdue" not in cli_mod.describe(d), cli_mod.describe(d)
    assert "overdue" in cli_mod.describe({**d, "reason": "overdue"})


# ── P6-2 ─────────────────────────────────────────────────────────────────────


def test_p6_2_g6_rl28_an_unreachable_job_is_never_quiet():
    """# RL28 P6-2 G6: 「조용함」도 마지막으로 들은 출력 시각으로 판정한 것이다 — 끊긴 잡에 내면
    웹 막대의 형편이 「조용함」이 된다. 대조: 같은 잡이 살아 있는 워커에 있으면 참이다."""
    j = job(933, key="deploy-dev", state=RUNNING, started_min=8, lane=1, worker_name="mac2")
    j = replace(j, last_output_at=ago(minutes=5))
    prog = {
        933: Progress(
            phase="executing",
            steps=(Step(index=1, name="fetch", state="done", ok=True, seconds=20.0),),
            steps_done=1,
            current_name="build",
            current_seconds=200.0,
        )
    }

    def one(lane: WorkerInfo):
        got = compute_queue(
            [j],
            workers=[lane],
            paused=False,
            medians={},
            presets=PRESETS,
            cfg=CFG,
            now=NOW,
            progress=prog,
        )
        return got[0]

    live = one(remote_lane("mac2", "busy", job_id=933))
    assert live.reason == "quiet" and live.estimate.quiet, live
    down = one(remote_lane("mac2", "down"))
    assert down.reason == GRACE
    assert down.estimate.quiet is False, down.estimate


def test_p6_2_rl45_eta_for_new_carries_the_presets_group_scope():
    """# RL45 P6-2: `rcm eta <preset>` 의 가상 잡은 제출된 잡처럼 프리셋의 그룹 범위를 단다. 그래서
    `host` 범위 그룹의 새 잡은 다른 호스트의 빈 레인에서 지금 시작한다고 본다 — 실제 제출과 같다.
    범위를 빼면 기본 `pool` 로 읽혀 같은 풀의 쥔 잡이 끝날 때까지 기다린다고 말한다(옛 값).
    대조: `pool` · `global` 범위는 같은 풀 안에서 쥔 잡을 기다린다."""
    lanes = [remote_lane("mac2", "busy", job_id=940), remote_lane("build-02", "idle")]

    def wait_for(scope: str) -> float | None:
        preset = replace(PRESETS["qa"], concurrency_scope=scope)
        holder = busy(940, key="qa", group="devices", concurrency_scope=scope)
        row, _ahead = eta_for_new(
            [holder],
            preset=preset,
            key="qa",
            workers=lanes,
            paused=False,
            medians={},
            presets={**PRESETS, "qa": preset},
            cfg=CFG,
            now=NOW,
        )
        return row.estimate.wait_seconds

    assert wait_for("host") == 0
    for scope in ("pool", "global"):
        got = wait_for(scope)
        assert got is not None and got > 0, (scope, got)
