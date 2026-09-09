"""표시 층(M5h · 순수) — 최근 줄의 스텝 문구 갈림 · 알림 env · 실패한 대기의 끝줄 · 코드 신원.

명세는 `docs/m5h-implementation.md` §1.5(문구 갈림 · `RCM_LAST_STEP`) · §2.5(`failure_lines`) ·
§4.1(`source_ident`). **구현보다 먼저 썼다(test-first) — 빨간 것이 정상이다.**

잠그는 모양:
- 최근 줄은 **선언된** `failed_step` 이면 `(step X)`, 없고 `last_step` 이면 `(last step X)`,
  둘 다 없으면 아무 괄호도 없다. 취소된 잡(#176)에는 두 칸이 다 비어 있어 라벨이 안 붙는다.
- `failure_lines()` 는 순수 함수다 — 잡 문서 하나와 번호·URL 만 받아 줄 목록을 만든다.
  종료 코드 1·2·3 을 가르지 않는다(그건 `cli._wait` 의 몫이다).
- `source_ident()` 는 목록 한 칸용 짧은 신원이고, 큐 행의 `_source_text()` 는 **안 건드린다**.

이 문서가 안 건드리는 것: `core/status.py` 의 `recent_json`/`progress_json` 이 `last_step` 을
싣는지(§1.4 — 역할 B 의 저장·서버 쪽 의존이다. 여기서는 그 키가 행에 있다고 **가정**한다) ·
`core/failures.py` 의 판정(§2.2, 역할 A) · 서버가 `failures` 를 만드는 자리(§2.3, 역할 B).
"""

from __future__ import annotations

from datetime import UTC
from typing import Any

import pytest

from jobfactory import NOW
from remote_ci_monitor.core.notify import notify_env
from remote_ci_monitor.core.render_text import (
    DASH,
    MAX_IDENT,
    failure_lines,
    render_pool,
    render_queue_row,
    source_ident,
)

# ── 도우미 ───────────────────────────────────────────────────────────────────

TREE_SOURCE = {
    "mode": "tree",
    "repo": "git@github.com:org/app",
    "base_sha": "25e1494ab0f1c2d3e4f5a6b7c8d9e0f1a2b3c4d5",
    "dirty": False,
    "branch": "chore/ci-guard",
    "tree_hash": "9f" * 32,
    "bytes": 100,
}


def recent_row(**over: Any) -> dict[str, Any]:
    """#162 를 본뜬 최근 행(스키마 v1 JSON). 렌더러는 dict 만 본다 — 모델을 안 거친다."""
    row: dict[str, Any] = {
        "id": 162,
        "state": "failed",
        "exit_code": 1,
        "key": "gate",
        "requester": {"name": "macbook", "label": "macbook@PCS"},
        "joiners": [],
        "summary": "exit 1",
        "failed_step": None,
        "last_step": None,
        "job_seconds": 660.0,
        "finished_at": "2026-09-04T00:50:00Z",
        "source": dict(TREE_SOURCE),
    }
    row.update(over)
    return row


def pool_doc(*, queue: list | None = None, recent: list | None = None) -> dict[str, Any]:
    return {
        "name": "default",
        "queue": queue if queue is not None else [],
        "recent": recent if recent is not None else [],
        "medians": {},
        "hosts": [],
    }


def recent_line(row: dict[str, Any]) -> str:
    """`render_pool` 이 그린 최근 줄 하나. 머리줄(`recent`)과 뒤 절은 뺀다."""
    out = render_pool(pool_doc(recent=[row]), tz=UTC, now=NOW)
    head = out.index("recent")
    lines = [ln for ln in out[head + 1 :] if ln.startswith("  ")]
    assert len(lines) == 1, out
    return lines[0]


def tail_of(row: dict[str, Any]) -> str:
    """최근 줄에서 시각 뒤의 꼬리(요약 + 스텝 라벨)."""
    return recent_line(row).split("  ")[-1]


def fail_item(
    name: str,
    *,
    seen: int,
    verdict: str,
    window: int = 8,
    window_unnamed: int = 0,
    step: bool = True,
) -> dict[str, Any]:
    """`GET /jobs/{id}` 의 `failures[]` 항목 하나(§2.2 의 키 이름 고정)."""
    return {
        "name": name,
        "step": step,
        "seen": seen,
        "window": window,
        "window_unnamed": window_unnamed,
        "first_seen_job_id": 141,
        "last_seen_job_id": 162,
        "verdict": verdict,
    }


def failed_job(failures: list[dict[str, Any]] | None = None, **over: Any) -> dict[str, Any]:
    """실패로 끝난 잡 문서. `failures` 가 None 이면 그 **키 자체를 안 넣는다**(조회 실패)."""
    job: dict[str, Any] = {
        "id": 162,
        "state": "failed",
        "exit_code": 1,
        "key": "gate",
        "summary": "exit 1",
        "url": "http://macmini:8787/#/jobs/162",
    }
    if failures is not None:
        job["failures"] = failures
    job.update(over)
    return job


# ── A. 최근 줄의 스텝 문구 갈림 (§1.5) ───────────────────────────────────────


def test_a_recent_row_with_a_declared_failed_step_says_step():
    """선언된 실패 스텝은 오늘과 같은 문면이다 — `(step test)`."""
    assert tail_of(recent_row(failed_step="test")) == "exit 1 (step test)"


def test_a_recent_row_with_only_a_last_step_says_last_step():
    """#162 처럼 아무도 실패를 선언 안 했으면 인과를 주장하지 않는다."""
    tail = tail_of(recent_row(last_step="build web"))
    assert tail == "exit 1 (last step build web)"
    assert "(step " not in tail


def test_a_recent_row_with_neither_step_shows_only_the_summary():
    """모르면 빈칸이다 — 괄호를 열지 않는다."""
    assert tail_of(recent_row()) == "exit 1"


def test_a_declared_failed_step_wins_over_the_last_step():
    """둘 다 있으면 선언이 이긴다(§1.5 의 갈림은 `if/else` 다)."""
    tail = tail_of(recent_row(failed_step="test", last_step="build web"))
    assert tail == "exit 1 (step test)"
    assert "last step" not in tail


def test_a_cancelled_row_carries_no_step_label_at_all():
    """#176 의 증인 — 취소된 잡은 두 칸이 다 비어 있어 요약 옆에 아무것도 안 붙는다(결정 64)."""
    row = recent_row(state="cancelled", exit_code=None, summary="cancelled by macbook")
    line = recent_line(row)
    assert line.endswith("cancelled by macbook")
    assert "(step" not in line and "(last step" not in line


# ── B. 알림 env (§1.5 `core/notify.py`) ──────────────────────────────────────


def test_notify_env_exports_the_last_step():
    """훅이 「어디까지 갔나」를 읽을 수 있어야 한다 — 새 키 하나."""
    env = notify_env({"id": 162, "state": "failed", "last_step": "build web"}, "slack")
    assert env["RCM_LAST_STEP"] == "build web"


def test_notify_env_never_fills_the_failed_step_from_the_last_step():
    """선언된 것만이다. 추론값을 env 에 흘리면 훅이 엉뚱한 스텝으로 커밋 status 를 남긴다."""
    env = notify_env(
        {"id": 162, "state": "failed", "failed_step": None, "last_step": "build web"}, "slack"
    )
    assert env["RCM_FAILED_STEP"] == ""
    assert env["RCM_LAST_STEP"] == "build web"


def test_notify_env_always_has_both_step_keys_and_sanitizes_the_last_step():
    """키 집합은 잡마다 같다(성공 잡도 빈 문자열) · 값은 다른 칸과 같은 규칙으로 정화한다."""
    green = notify_env({"id": 1, "state": "succeeded"}, "slack")
    assert green["RCM_LAST_STEP"] == "" and green["RCM_FAILED_STEP"] == ""
    dirty = notify_env({"id": 2, "state": "failed", "last_step": "bui\x07ld" + "x" * 5000}, "slack")
    assert "\x07" not in dirty["RCM_LAST_STEP"]
    assert len(dirty["RCM_LAST_STEP"].encode()) <= 4096


# ── C. `failure_lines()` (§2.5 · 순수) ───────────────────────────────────────


def test_the_log_line_names_the_command_and_the_url():
    """모를수록 로그가 필요하다 — 이름이 하나도 없어도 길은 알려 준다."""
    lines = failure_lines(failed_job(), job_id=162, url="http://macmini:8787/#/jobs/162")
    assert lines == ["log: rcm logs 162 · http://macmini:8787/#/jobs/162"]


def test_the_log_line_without_a_url_is_just_the_command():
    """서버 주소를 모르는 세션(`--server` 없이 discover)이라도 줄이 깨지지 않는다."""
    assert failure_lines(failed_job(), job_id=162, url=None) == ["log: rcm logs 162"]


def test_an_empty_failure_list_is_not_the_same_as_a_missing_one_but_prints_the_same_line():
    """`failures: []` 는 「이름이 없다」, 키 없음은 「못 읽었다」 — 둘 다 이름 줄은 없다."""
    lines = failure_lines(failed_job([]), job_id=162, url=None)
    assert lines == ["log: rcm logs 162"]


def test_a_persistent_name_says_every_one_of_the_last_runs():
    rows = [fail_item("test", seen=8, window=8, verdict="persistent")]
    lines = failure_lines(failed_job(rows), job_id=162, url=None)
    assert lines[1] == "failed: test — every one of the last 8 gate runs"


def test_an_intermittent_name_keeps_the_question_mark():
    """물음표는 계약이다 — 판정이 아니라 제안이다(§2.5)."""
    rows = [fail_item("port_test.dart", seen=3, window=8, verdict="intermittent")]
    lines = failure_lines(failed_job(rows), job_id=162, url=None)
    assert lines[1] == "failed: port_test.dart — 3 of the last 8 gate runs · intermittent?"


def test_a_first_seen_name_says_first_time():
    rows = [fail_item("new_test", seen=1, window=8, verdict="first_seen")]
    lines = failure_lines(failed_job(rows), job_id=162, url=None)
    assert lines[1] == "failed: new_test — first time in the last 8 gate runs"


def test_a_shallow_window_says_so_far_and_never_judges():
    """창이 얕으면(`window < min_jobs`) 숫자만 말한다 — 「간헐」이라고 부르지 않는다."""
    rows = [fail_item("test", seen=1, window=2, verdict="unknown")]
    lines = failure_lines(failed_job(rows), job_id=162, url=None)
    assert lines[1] == "failed: test — 1 of 2 gate runs so far"
    assert "intermittent" not in lines[1]


def test_the_name_lines_keep_the_jobs_own_order_and_stop_at_three():
    """이름 순이 아니라 잡이 찍은 순서(seq)다. 넘치면 세다 말고 로그로 보낸다."""
    names = ["e", "d", "c", "b", "a"]
    rows = [fail_item(n, seen=2, window=8, verdict="intermittent") for n in names]
    lines = failure_lines(failed_job(rows), job_id=162, url=None)
    assert [ln.split(" — ")[0] for ln in lines[1:4]] == ["failed: e", "failed: d", "failed: c"]
    assert lines[4] == "… and 2 more (rcm logs 162)"
    assert len(lines) == 5


def test_exactly_three_names_need_no_more_line():
    """경계 — 상한과 같으면 「더 있다」가 아니다."""
    rows = [fail_item(n, seen=2, window=8, verdict="intermittent") for n in ("a", "b", "c")]
    lines = failure_lines(failed_job(rows), job_id=162, url=None)
    assert len(lines) == 4
    assert not any("more" in ln for ln in lines)


def test_the_limit_is_an_argument_and_the_remainder_is_counted_from_it():
    rows = [fail_item(n, seen=2, window=8, verdict="intermittent") for n in "abcde"]
    lines = failure_lines(failed_job(rows), job_id=162, url=None, limit=1)
    assert lines[1].startswith("failed: a")
    assert lines[2] == "… and 4 more (rcm logs 162)"


def test_an_unnamed_failure_in_the_window_is_declared_as_a_note():
    """분모의 품질을 밝힌다(결정 68) — 분자는 과소일지언정 과대는 아니다."""
    rows = [fail_item("test", seen=2, window=8, window_unnamed=2, verdict="intermittent")]
    lines = failure_lines(failed_job(rows), job_id=162, url=None)
    assert lines[-1] == "note: 2 of those 8 runs failed without naming anything"


def test_a_clean_window_gets_no_note_line():
    rows = [fail_item("test", seen=2, window=8, window_unnamed=0, verdict="intermittent")]
    lines = failure_lines(failed_job(rows), job_id=162, url=None)
    assert not any(ln.startswith("note:") for ln in lines)
    assert len(lines) == 2


def test_a_succeeded_job_gets_no_lines_at_all():
    """성공한 잡에는 로그 길도 안 적는다 — 끝줄은 실패의 것이다."""
    green = {"id": 163, "state": "succeeded", "exit_code": 0, "key": "gate", "summary": "green"}
    assert failure_lines(green, job_id=163, url="http://macmini:8787/#/jobs/163") == []


# ── D. `source_ident()` (§4.1 · 순수) ────────────────────────────────────────


def test_a_git_ref_source_is_the_ref_and_a_short_sha():
    src = {"mode": "git_ref", "repo": "org/app", "ref": "main", "sha": "25e1494ab0f1c2d3"}
    assert source_ident(src) == "main @25e1494"


def test_a_tree_source_with_a_branch_is_the_branch_and_a_short_base_sha():
    assert source_ident(TREE_SOURCE) == "chore/ci-guard @25e1494"


def test_a_dirty_tree_gets_a_plus_after_the_sha():
    """`+` 는 「올린 트리가 그 커밋이 아니다」는 뜻이다 — 자리는 sha 뒤다."""
    assert source_ident({**TREE_SOURCE, "dirty": True}) == "chore/ci-guard @25e1494+"


@pytest.mark.parametrize(("dirty", "expected"), [(False, "app @25e1494"), (True, "app @25e1494+")])
def test_a_tree_without_a_branch_falls_back_to_the_last_piece_of_the_repo(dirty, expected):
    """옛 잡(브랜치를 안 보내던 클라이언트)도 자기 코드를 말한다."""
    src = {**TREE_SOURCE, "branch": None, "repo": "git@github.com:org/app", "dirty": dirty}
    assert source_ident(src) == expected


@pytest.mark.parametrize("src", [None, {}, {"mode": "tree"}, {"mode": "git_ref"}])
def test_a_source_without_anything_to_say_is_a_dash(src):
    """모르는 값은 `—` 다 — 빈칸도 0 도 아니다(집안 규칙)."""
    assert source_ident(src) == DASH


def test_a_long_identity_is_cut_from_the_back_and_marked():
    """목록의 칸 하나다 — 길면 앞을 남기고 잘렸다고 말한다."""
    src = {**TREE_SOURCE, "branch": "feature/a-very-long-branch-name-that-runs-on"}
    full = "feature/a-very-long-branch-name-that-runs-on @25e1494"
    out = source_ident(src)
    assert len(out) == MAX_IDENT and out.endswith("…")
    assert out == full[: MAX_IDENT - 1] + "…"


def test_an_identity_of_exactly_thirty_two_characters_is_left_alone():
    """경계 — 상한과 같으면 자르지 않는다."""
    branch = "b" * (MAX_IDENT - len(" @25e1494"))
    out = source_ident({**TREE_SOURCE, "branch": branch})
    assert out == f"{branch} @25e1494" and len(out) == MAX_IDENT
    assert "…" not in out


def test_no_branch_ever_makes_the_column_wider_than_the_limit():
    """표의 칸이 흔들리면 목록이 읽히지 않는다 — 모든 갈래에 대한 상한이다."""
    sources = [
        TREE_SOURCE,
        {**TREE_SOURCE, "dirty": True, "branch": "x" * 200},
        {**TREE_SOURCE, "branch": None, "repo": "git@github.com:org/" + "y" * 200},
        {"mode": "git_ref", "repo": "org/app", "ref": "z" * 200, "sha": "25e1494ab0f1"},
        {},
    ]
    assert all(len(source_ident(s)) <= MAX_IDENT for s in sources)


# ── E. 그 칸이 놓이는 자리 (§4.1 표시) ───────────────────────────────────────


def test_the_recent_line_says_which_code_ran():
    """`rcm top` 의 최근 줄도 코드 신원을 말한다 — 오늘은 요청자까지만 있다."""
    line = recent_line(recent_row(last_step="build web"))
    assert "chore/ci-guard @25e1494" in line
    assert line.index("macbook@PCS") < line.index("chore/ci-guard @25e1494")
    assert line.index("chore/ci-guard @25e1494") < line.index("(last step build web)")


def test_the_queue_row_still_uses_the_long_source_text():
    """큐 행은 `_source_text()` 전용이다(§4.1) — 한 글자도 안 바뀐다."""
    row = {
        "id": 412,
        "state": "queued",
        "position": 1,
        "key": "gate",
        "requester": {"label": "macbook@PCS"},
        "source": {**TREE_SOURCE, "dirty": True},
        "estimate": {},
        "reason": "waiting_for_lane",
    }
    line = render_queue_row(row, UTC, NOW)[0]
    assert "git@github.com:org/app @25e1494+uncommitted" in line
