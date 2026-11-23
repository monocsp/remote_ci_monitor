"""M5s G — 화면에 보이는 예약 · 표식(`docs/m5s-workplan.md` §4.1 「표시」 · §4.3 · §5 G 행).

- 웹의 `ACTIONABLE`(app.js)과 모델의 `ACTIONABLE_REASONS` 는 같은 목록이다. 서버가 새 이유를
  올렸는데 화면이 모르면 「Needs a look」에서 조용히 빠진다 — 그 틈을 문자열 비교로 잠근다.
- `rcm top` 은 풀마다 예약 한 줄을 그린다. 읽지 못했으면 「unavailable」이지 아무 줄도 없는 것이
  아니다(fail-open 금지). 예약이 없거나 옛 서버면 줄을 늘리지 않는다.
- `rcm wait` 의 「needs action」 줄은 아직 도는 잡에만 찍는다. 끝난 잡은 더 받을 사람이 없다
  (#251 재검증 P3).
- `rcm top` 의 needs action 문구는 note 를 `clean_line` 으로 한 번 더 씻는다.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from fake_rcm_http import FakeRcm
from remote_ci_monitor import __version__
from remote_ci_monitor import cli as cli_mod
from remote_ci_monitor.core import model
from remote_ci_monitor.core.render_text import _reason_text, render
from test_cli_m1 import last_json

ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "src" / "remote_ci_monitor" / "web" / "app.js"
NOW = "2026-11-23T05:00:00Z"  # 서울 14:00
LOGIN = {"code": "login", "note": "Sign in on the build machine", "at": "2026-11-23T04:58:00Z"}


# ── 웹 ACTIONABLE = 모델 ACTIONABLE_REASONS ─────────────────────────────────


def web_actionable() -> tuple[str, ...]:
    m = re.search(r"var ACTIONABLE = \[([^\]]*)\];", APP_JS.read_text())
    assert m, "app.js has no `var ACTIONABLE = [...]`"
    return tuple(re.findall(r'"([a-z_]+)"', m.group(1)))


def test_web_actionable_is_the_models_actionable_reasons_in_the_same_order():
    assert web_actionable() == model.ACTIONABLE_REASONS
    assert web_actionable()[0] == model.REASON_NEEDS_ACTION


def web_set(name: str) -> frozenset[str]:
    m = re.search(r"var " + name + r" = \{([^}]*)\};", APP_JS.read_text())
    assert m, f"app.js has no `var {name} = {{...}}`"
    return frozenset(re.findall(r"([a-z_]+): 1", m.group(1)))


def test_web_store_check_skips_the_models_not_yet_a_result_states():
    """Store 탭의 「가장 새 조회」는 예약과 사슬의 중간 시도를 건너뛴다 — 모델의 두 집합과 같다."""
    assert web_set("STORE_CHECK_NOT_A_RESULT") == model.SCHEDULED_STATES | model.CHAIN_LINK_STATES


def test_web_terminal_is_the_models_terminal_states():
    assert web_set("TERMINAL") == model.TERMINAL_STATES


# ── rcm top 의 예약 한 줄 ────────────────────────────────────────────────────


def sched(job_id: int, not_before: str, preset: str = "golive", **over: Any) -> dict[str, Any]:
    row = {
        "id": job_id,
        "preset": preset,
        "key": f"{preset}:1.2.0",
        "state": "scheduled",
        "not_before": not_before,
        "start_deadline": None,
        "requester": {"name": "alice-laptop", "label": "alice@laptop"},
    }
    row.update(over)
    return row


def status(**pool: Any) -> dict[str, Any]:
    base = {"name": "default", "queue": [], "recent": [], "medians": {}, "hosts": []}
    base.update(pool)
    return {
        "schema_version": 1,
        "generated_at": NOW,
        "display_timezone": "Asia/Seoul",
        "server": {"lanes": 2, "workers": []},
        "pools": [base],
    }


def scheduled_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.startswith("scheduled")]


def test_top_has_one_scheduled_line_with_the_count_and_the_next_one():
    doc = status(
        scheduled=[
            sched(31, "2026-11-23T08:00:00Z", preset="gate"),
            sched(30, "2026-11-23T06:00:00Z"),  # 서버는 시각순으로 주지만 화면이 다시 고른다
        ],
        scheduled_error=None,
    )
    assert scheduled_lines(render(doc)) == ["scheduled — 2 · next 15:00 golive"]


def test_top_says_unavailable_when_schedules_could_not_be_read():
    doc = status(scheduled=None, scheduled_error="database is locked")
    assert scheduled_lines(render(doc)) == ["scheduled — unavailable: database is locked"]
    # 목록과 오류가 함께 오면 목록을 믿지 않는다
    doc = status(scheduled=[sched(1, "2026-11-23T06:00:00Z")], scheduled_error="partial read")
    assert scheduled_lines(render(doc)) == ["scheduled — unavailable: partial read"]


@pytest.mark.parametrize("pool", [{"scheduled": [], "scheduled_error": None}, {}])
def test_top_adds_no_line_without_schedules_or_on_an_old_server(pool):
    assert scheduled_lines(render(status(**pool))) == []


def test_top_names_the_other_pool_on_its_scheduled_line():
    doc = status(scheduled=[], scheduled_error=None)
    doc["pools"].append(
        {
            "name": "linux",
            "lanes": 1,
            "queue": [],
            "recent": [],
            "medians": {},
            "hosts": [],
            "scheduled": [sched(40, "2026-11-23T06:30:00Z", preset="nightly", pool="linux")],
            "scheduled_error": None,
        }
    )
    assert scheduled_lines(render(doc)) == ["scheduled — 1 · next 15:30 nightly (pool linux)"]


def test_top_marks_a_schedule_on_another_day_with_its_date():
    doc = status(scheduled=[sched(1, "2026-11-24T00:30:00Z")], scheduled_error=None)
    assert scheduled_lines(render(doc)) == ["scheduled — 1 · next Nov 24 · 09:30 golive"]


def test_top_draws_expired_and_exhausted_with_their_own_glyph_in_recent():
    """`?` 는 「결과를 잃음」 자리다 — 만료(돌지 않았다)와 시도를 다 씀(실패와 같은 줄)이 그 모양을
    빌리면 터미널에서 셋이 갈리지 않는다."""
    rows = [
        {"id": 1, "state": "expired", "key": "gate", "requester": {"label": "a"}},
        {"id": 2, "state": "exhausted", "key": "golive", "requester": {"label": "a"}},
        {"id": 3, "state": "lost", "key": "qa", "requester": {"label": "a"}},
    ]
    lines = render(status(recent=rows)).splitlines()
    glyph = {
        r["id"]: next(ln for ln in lines if r["key"] in ln and r["state"] in ln).split()[0]
        for r in rows
    }
    assert glyph[1] != glyph[3] and glyph[2] != glyph[3], glyph
    assert glyph[2] == "❌", "시도를 다 씀은 실패와 같은 줄이다(종료 코드 1)"


# ── rcm top 의 needs action 문구 ─────────────────────────────────────────────


def test_top_cleans_the_note_once_more():
    """옛 서버나 가짜 서버의 문서일 수 있다 — `rcm wait` 의 `action_line` 과 같은 규칙."""
    note = "Sign‮ in\x1b[2J​ now"
    row = {"id": 7, "state": "running", "reason": "needs_action", "lane": 1}
    text = _reason_text({**row, "action": {"code": "login", "note": note, "at": None}})
    assert text == "needs action · login · Sign in[2J now"


# ── rcm wait: 끝난 잡에는 「needs action」 줄을 찍지 않는다 (#251 재검증 P3) ──


@pytest.mark.parametrize("state", ["failed", "timed_out", "succeeded", "lost", "expired"])
def test_a_finished_job_is_not_announced_and_never_returns_4(state):
    watch = cli_mod._ActionWatch(41, return_on_action=True)
    doc = {"id": 41, "state": state, "action": LOGIN}
    assert watch.observe(doc) is None
    assert watch.stop_when(doc) is None


@pytest.mark.parametrize("state", ["running", "queued", "scheduled", "uploading"])
def test_a_job_that_can_still_move_is_announced(state):
    watch = cli_mod._ActionWatch(41, return_on_action=True)
    doc = {"id": 41, "state": state, "action": LOGIN}
    assert watch.observe(doc) == "#41 needs action · login · Sign in on the build machine"
    assert watch.stop_when(doc) == cli_mod.EXIT_ACTION


def test_every_terminal_state_and_cancelling_count_as_stopped():
    for state in (*model.TERMINAL_STATES, model.CANCELLING):
        assert cli_mod._ActionWatch._stopped({"state": state}), state


# ── 반복 사슬: 다음 시도가 표식을 이어받아 4 (M5s 결정 233 · 234, D 와 합친 뒤) ──


@pytest.fixture
def chain_fake(monkeypatch, tmp_path):
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


def _attempt(jid: int, state: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": jid,
        "state": state,
        "preset": "golive",
        "key": "golive",
        "requester": {"name": "alice", "label": "alice@laptop"},
        "source": {"mode": "git_ref", "ref": "main", "sha": "0" * 40},
        "chain_id": 57,
        "max_attempts": 12,
        **extra,
    }


def test_return_on_action_returns_4_on_the_next_attempt_that_carries_the_chain_action(
    chain_fake, capsys
):
    """75 로 끝난 시도(`retried`)는 종료 상태라 거기서는 4 가 나오지 않는다. 표식은 사슬의 것이라
    다음 시도(예약으로 기다리는 중)가 `needs_action` 과 그 `action` 을 이어받고, 거기서 4 다."""
    chain_fake.route(
        "GET",
        "/jobs/57",
        _attempt(57, "retried", attempt=1, exit_code=75, retried_as=58, action=LOGIN),
    )
    chain_fake.route(
        "GET",
        "/jobs/58",
        _attempt(58, "scheduled", attempt=2, retry_of=57, reason="needs_action", action=LOGIN),
    )
    try:
        code = cli_mod.main(["wait", "--job", "57", "--poll", "--return-on-action"])
    except SystemExit as e:
        code = e.code
    cap = capsys.readouterr()
    assert code == 4, cap.err
    body = last_json(cap.out)
    assert body["job_id"] == 58 and body["action"] == LOGIN and body["wait_exit_code"] == 4
    lines = [ln for ln in cap.err.splitlines() if "needs action" in ln]
    assert lines == ["#58 needs action · login · Sign in on the build machine"], cap.err
