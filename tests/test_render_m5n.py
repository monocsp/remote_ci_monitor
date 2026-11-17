"""`rcm top` 의 대기 행(M5n 역할 C) — `any machine` · `no capable machine — …` · 점검 문구 ·
행동 가능한 이유의 순서. 명세는 docs/m5n-workplan.md §7.3 · §8.2 · §12.

순수 렌더(`core/render_text.render`)를 손으로 쓴 스키마 v1 문서로 부른다 — HTTP 도 시계도 없다.
**구현보다 먼저 썼다(test-first) — 빨간 것이 정상이다.**

대기 행의 둘째 줄(8칸 들여쓰기)이 이유 줄이다(`render_queue_row`). 이유 줄의 글자를 통째로 잠근다.
"""

from __future__ import annotations

from datetime import UTC
from typing import Any

import pytest

from remote_ci_monitor.core import model
from remote_ci_monitor.core.render_text import render

NOW = "2026-11-16T09:00:00Z"


def row(job_id: int, **over: Any) -> dict[str, Any]:
    r: dict[str, Any] = {
        "id": job_id,
        "pool": "default",
        "state": "queued",
        "key": "gate",
        "requester": {"name": "alice", "label": "alice@mbp"},
        "joiners": [],
        "source": {"mode": "git_ref", "repo": "org/app", "ref": "main", "sha": "25e1494ab0f1c2d3"},
        "estimate": {"waited_seconds": 30.0, "source": "default", "sample_count": 0},
        "reason": "waiting_for_lane",
        "placement": "pool",
        "placement_detail": [],
        "position": 1,
    }
    r.update(over)
    return r


def note(worker: str, state: str, code: str, name: str | None = None, **detail: Any) -> dict:
    return {
        "worker": worker,
        "display_name": name or worker,
        "state": state,
        "code": code,
        "detail": detail,
    }


def doc(*queue: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": NOW,
        "display_timezone": "UTC",
        "server": {"lanes": 2, "workers": []},
        "pools": [
            {"name": "default", "queue": list(queue), "recent": [], "medians": {}, "hosts": []}
        ],
    }


def block(text: str, job_id: int) -> list[str]:
    """`#<id>` 가 든 머리줄과 그 아래 들여쓴 줄들 — 한 잡의 덩어리."""
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


def reason_line(text: str, job_id: int) -> str:
    return block(text, job_id)[1].strip()


def top(*queue: dict[str, Any]) -> str:
    return render(doc(*queue), tz=UTC)


# ── 대기 행 ──────────────────────────────────────────────────────────────────


def test_an_any_capable_waiting_row_says_any_machine():
    """대기 행의 메모에는 eligible 워커도 실린다(가정 B-15) — 이유 줄에 늘어놓지 않는다."""
    eligible = [note("@local", "eligible", "ok", "Mac mini")]
    text = top(row(3701, placement="capable", placement_detail=eligible))
    assert reason_line(text, 3701) == "waiting for lane · any machine", text


def test_a_pool_row_does_not_say_any_machine():
    text = top(row(3702))
    assert "any machine" not in "\n".join(block(text, 3702)), text


def test_no_capable_machine_lists_each_machine_with_its_reason():
    """§2 S4 의 글자 그대로. down 은 코드가 무엇이든 `down` 이다."""
    detail = [
        note("mbp", "ineligible", "tool_missing", tools=["fvm"]),
        note("mac2-worker", "down", "worker_down"),
    ]
    r = row(3703, placement="capable", reason="no_capable_machine", placement_detail=detail)
    text = top(r)
    assert reason_line(text, 3703) == "no capable machine — mbp: fvm missing · mac2-worker: down"


def test_no_capable_machine_without_detail_says_unknown():
    """§12 — 사유를 모르면 지어내지 않는다."""
    r = row(3704, placement="capable", reason="no_capable_machine", placement_detail=[])
    assert reason_line(top(r), 3704) == "no capable machine — unknown"


def test_no_capable_machine_has_no_eta():
    r = row(
        3705,
        placement="capable",
        reason="no_capable_machine",
        placement_detail=[note("mbp", "ineligible", "tool_missing", tools=["fvm"])],
    )
    assert "eta —" in block(top(r), 3705)[0]


@pytest.mark.parametrize(
    ("reason", "detail", "want"),
    [
        (
            "check_failed",
            [
                note(
                    "@local",
                    "ineligible",
                    "check_failed",
                    "Mac mini",
                    check="sim-check",
                    job_id=3620,
                )
            ],
            "check failed — Mac mini: sim-check failed #3620",
        ),
        (
            "check_pending",
            [
                note(
                    "@local", "unknown", "check_running", "Mac mini", check="sim-check", job_id=3650
                )
            ],
            "check pending — Mac mini: sim-check running",
        ),
        (
            "check_pending",
            [note("mbp", "unknown", "check_needed", check="sim-check")],
            "check pending — mbp: need sim-check",
        ),
    ],
)
def test_check_reasons_name_the_machine_and_the_check(reason, detail, want):
    r = row(3706, key="scenario-qa", reason=reason, placement_detail=detail)
    assert reason_line(top(r), 3706) == want


@pytest.mark.parametrize(
    ("code", "detail", "text"),
    [
        ("tool_missing", {"tools": ["fvm", "gitleaks"]}, "fvm, gitleaks missing"),
        ("workspace_tool", {"tools": ["melos"]}, "melos workspace only"),
        ("check_failed", {"check": "sim-check"}, "sim-check failed"),
        ("report_missing", {}, "no report"),
        ("report_stale", {}, "stale report"),
        ("fingerprint_unknown", {}, "fingerprint unknown"),
        ("some_future_code", {}, "some_future_code"),
    ],
)
def test_each_verdict_code_has_one_short_text(code, detail, text):
    """기계별 짧은 사유(가정 C3). 모르는 코드는 코드 그대로 — 빈칸보다 낫다."""
    r = row(
        3707,
        placement="capable",
        reason="no_capable_machine",
        placement_detail=[note("mbp", "unknown", code, **detail)],
    )
    assert reason_line(top(r), 3707) == f"no capable machine — mbp: {text}"


def test_a_targeted_check_job_names_its_machine():
    """지정 잡(§4.5)은 어느 머신에서 도는지가 곧 이유다."""
    r = row(3708, key="sim-check", target_worker="mbp", placement="pool")
    assert reason_line(top(r), 3708) == "waiting for lane · on mbp"


# ── 행동 가능한 이유의 순서 (§7.3) ───────────────────────────────────────────


def test_actionable_order_puts_the_new_reasons_right_after_worker_down():
    assert model.ACTIONABLE_REASONS == (
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


def test_check_pending_is_not_actionable():
    """스스로 풀린다 — `held_by_load` 와 같은 종류(§7.3)."""
    assert model.REASON_CHECK_PENDING == "check_pending"
    assert model.REASON_CHECK_PENDING not in model.ACTIONABLE_REASONS


def test_reason_constants_are_the_wire_values():
    assert model.REASON_NO_CAPABLE_MACHINE == "no_capable_machine"
    assert model.REASON_CHECK_FAILED == "check_failed"
