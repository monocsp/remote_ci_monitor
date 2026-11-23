"""실패를 이름으로 말한다 — M5o B, `docs/m5o-workplan.md` §3.1 · 결정 117·118.

`docs/m5o-test-scenarios-b.md` 의 요약 표(B1~B9 · B14~B19)다. 핵심은 세 가지다.

1. `exit_code` 요약이 실패 이름과 스텝을 알면 그것을 먼저 말한다. **코드는 그대로**이고 인자만 는다
   — `summary_code == "exit_code"` 로 거르던 스크립트가 깨지지 않는다.
2. 로컬 레인과 원격 워커 finish 가 **같은 글자 · 같은 인자**를 남긴다. 어느 레인이 집었나가 요약을
   바꾸면 사람은 같은 실패를 두 가지로 읽는다.
3. 인자는 공개 문서(`/api/status`)에 실린다 — 경로는 마지막 조각만, 제어 · 보이지 않는 문자는
   지우고, 80자에서 자른다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from remote_ci_monitor.config import parse_preset
from remote_ci_monitor.core import outcome
from remote_ci_monitor.core.model import FAILED, SUCCEEDED, TIMED_OUT
from remote_ci_monitor.core.progress import MAX_FAIL_NAMES, Marker
from remote_ci_monitor.store import Store
from remote_ci_monitor.worker import outcome_for
from test_worker import enqueue, make_config, run_one, sh
from test_worker_api import WorkerServer

T0 = datetime(2026, 11, 18, 9, 0, 0, tzinfo=UTC)

#: 운영 잡 #3760 의 모양 — 단위 이름 둘을 찍고 스텝 이름도 실패로 선언했다(`job_failures` 의 seq 순)
GATE_LOG = (
    "::rcm::step::analyze\n"
    "::rcm::step::test\n"
    "::rcm::fail::test/app/character_name_i18n_contract_test.dart\n"
    "::rcm::fail::test/feature/home/home_dudi_bubble_gap_golden_test.dart\n"
    "::rcm::fail::test\n"
)
GATE_TEXT = "test: character_name_i18n_contract_test.dart +1 more (exit 1)"
GATE_ARGS = {
    "code": 1,
    "step": "test",
    "first": "character_name_i18n_contract_test.dart",
    "more": 1,
}


def markers(*pairs: tuple[str, str]) -> list[Marker]:
    return [Marker(at=T0 + timedelta(seconds=i), kind=k, value=v) for i, (k, v) in enumerate(pairs)]


class _Job:
    """`outcome_for` 가 읽는 칸만 가진 잡."""

    cancel = None
    cancelled_by = None  # 취소 요약이 밀려난 잡인지 볼 때 읽는다(M5o A `worker.cancel_outcome`)
    summary_code = None
    summary_args: dict = {}
    timeout_seconds = 60

    def retries_on(self, rc):  # 반복 정책이 없는 잡(M5s) — 75 도 여느 실패다
        return False


def finish(ms: list[Marker], rc: int | None = 1, **kw):
    return outcome_for(_Job(), ms, started=T0, finished=T0 + timedelta(minutes=5), rc=rc, **kw)


# ── exit_summary: 순수 ─────────────────────────────────────────────────────────


def test_b1_step_and_two_unit_names_name_the_first_and_count_the_rest():
    text, code, args = outcome.exit_summary(
        1,
        "test",
        ["test/a/x_test.dart", "test/b/y_test.dart", "test"],
        ["analyze", "test"],
    )
    assert text == "test: x_test.dart +1 more (exit 1)"
    assert code == "exit_code"  # 결정 117 — 코드는 그대로
    assert args == {"code": 1, "step": "test", "first": "x_test.dart", "more": 1}
    assert outcome.render(code, args) == text  # 화면이 코드로 다시 그려도 같은 글자


def test_b1b_one_unit_name_has_no_more():
    text, _, args = outcome.exit_summary(1, "test", ["test/a/x_test.dart"], ["test"])
    assert text == "test: x_test.dart (exit 1)"
    assert "more" not in args  # 0 은 싣지 않는다


def test_b2_only_the_step_says_the_step_failed():
    text, code, args = outcome.exit_summary(1, "analyze", ["analyze"], ["analyze"])
    assert text == "analyze failed (exit 1)"
    assert code == "exit_code" and args == {"code": 1, "step": "analyze"}


def test_b2b_unit_names_without_a_declared_step():
    text, _, args = outcome.exit_summary(1, None, ["a/x_test.dart", "b/y_test.dart"], [])
    assert text == "x_test.dart +1 more (exit 1)"
    assert args == {"code": 1, "first": "x_test.dart", "more": 1}


def test_b2c_failed_step_names_are_not_counted_as_units():
    text, _, args = outcome.exit_summary(1, "analyze", ["analyze", "test"], ["analyze", "test"])
    assert text == "analyze failed (exit 1)"
    assert "more" not in args and "first" not in args


def test_b3_nothing_known_is_todays_text_and_todays_args():
    assert outcome.exit_summary(1, None, [], []) == ("exit 1", "exit_code", {"code": 1})
    assert outcome.exit_summary(3, None, (), ()) == outcome.summary("exit_code", code=3)


def test_b5_a_long_path_keeps_its_last_segment_cut_at_80():
    name = "test/" + "deep/" * 20 + "n" * 100 + "_test.dart"
    text, _, args = outcome.exit_summary(1, "test", [name], ["test"])
    assert args["first"] == ("n" * 100 + "_test.dart")[: outcome.MAX_FAIL_ARG]
    assert outcome.MAX_FAIL_ARG == 80 and len(args["first"]) == 80
    assert "/" not in args["first"] and len(text) <= outcome.MAX_SUMMARY


def test_b5b_backslash_and_trailing_separators_still_find_the_name():
    assert outcome.exit_summary(1, None, ["dir\\sub\\x_test.dart"], [])[2]["first"] == "x_test.dart"
    assert outcome.exit_summary(1, None, ["lib/widgets/"], [])[2]["first"] == "widgets"


def test_b6_truncated_names_never_claim_an_exact_count():
    names = [f"t/u{i}_test.dart" for i in range(MAX_FAIL_NAMES)]
    text, _, args = outcome.exit_summary(1, "test", names, ["test"], truncated=True)
    assert text == "test: u0_test.dart +99+ more (exit 1)"
    assert args["more"] == 99 and args["more_truncated"] is True
    assert outcome.render("exit_code", args) == text


def test_b8_control_and_invisible_characters_are_dropped_from_the_names():
    text, _, args = outcome.exit_summary(1, "te‮st", ["a/x‮_te\x1bst.dart", "​"], ["te‮st"])
    assert args["first"] == "x_test.dart" and args["step"] == "test"
    assert "more" not in args  # 지우고 나서 빈 이름은 이름이 아니다
    assert text == "test: x_test.dart (exit 1)"
    assert all(ch.isprintable() for ch in text)


def test_b17_a_long_step_name_is_cut_and_the_sentence_stays_bounded():
    step = "회" * 120
    text, _, args = outcome.exit_summary(1, step, ["x" * 120], [step])
    assert len(args["step"]) == 80 and len(args["first"]) == 80
    assert len(text) <= outcome.MAX_SUMMARY


def test_timed_out_names_where_it_was_without_claiming_cause():
    text, code, args = outcome.summary("timed_out", seconds=2400, last_step="test")
    assert (text, code, args) == (
        "limit 40m (in test)",
        "timed_out",
        {"seconds": 2400, "last_step": "test"},
    )
    assert outcome.summary("timed_out", seconds=2400)[0] == "limit 40m"


# ── clean_args: 공개 인자의 경계 ──────────────────────────────────────────────


def test_result_args_are_shaped_like_the_preflight_args():
    """서버 안의 생산자가 실수로 경로 · 제어 문자 · 엉뚱한 타입을 넘겨도 공개 문서엔 모양대로만."""
    clean = outcome.clean_args(
        "exit_code",
        {
            "code": 1,
            "step": "te\x07st",
            "first": "/home/alice/secret/x_test.dart",
            "more": "lots",
            "more_truncated": "yes",
            "note": "free text",
        },
    )
    assert clean == {"code": 1, "step": "test", "first": "x_test.dart"}
    clean = outcome.clean_args("timed_out", {"seconds": 60, "last_step": "a\nb", "x": 1})
    assert clean == {"seconds": 60, "last_step": "ab"}


def test_the_worker_still_cannot_choose_exit_code_at_the_boundary():
    """B19 — 워커가 고를 수 있는 코드는 시작 전 실패뿐이다. 인자 표를 넓혔다고 경계가
    넓어지지 않는다."""
    with pytest.raises(outcome.OutcomeError):
        outcome.clean_args("exit_code", {"code": 1}, strict=True)


# ── outcome_for: 로컬 · 원격이 함께 쓰는 종료 규칙 ─────────────────────────────


def test_outcome_for_uses_the_names_the_job_printed():
    ms = markers(
        ("step", "analyze"),
        ("step", "test"),
        ("fail", "test/app/character_name_i18n_contract_test.dart"),
        ("fail", "test/feature/home/home_dudi_bubble_gap_golden_test.dart"),
        ("fail", "test"),
    )
    oc = finish(ms)
    assert oc.state == FAILED and oc.summary == GATE_TEXT
    assert oc.code == "exit_code" and oc.args == GATE_ARGS


def test_b4_the_jobs_own_summary_still_wins():
    oc = finish(markers(("step", "test"), ("fail", "x_test.dart"), ("summary", "2 tests failed")))
    assert oc.summary == "2 tests failed" and oc.code is None and oc.args == {}


def test_b6_outcome_for_carries_the_truncation():
    units = [("fail", f"u{i}.dart") for i in range(MAX_FAIL_NAMES + 1)]
    oc = finish(markers(("step", "test"), ("fail", "test"), *units))
    assert oc.fail_truncated and oc.failed_step == "test"
    # 대장에 든 것은 스텝 하나 + 단위 99개다. 첫 단위를 빼면 98, 그 뒤는 모른다
    assert oc.summary == f"test: u0.dart +{MAX_FAIL_NAMES - 2}+ more (exit 1)"


def test_b9_timed_out_says_the_last_step():
    oc = finish(markers(("step", "build"), ("step", "test")), rc=-9, timed_out=True)
    assert oc.state == TIMED_OUT and oc.summary == "limit 1m (in test)"
    assert oc.args == {"seconds": 60, "last_step": "test"}
    assert finish([], rc=-9, timed_out=True).summary == "limit 1m"  # B9b — 오늘과 같다


def test_b14_a_green_job_gets_nothing_added():
    oc = finish(markers(("step", "test"), ("fail", "x_test.dart")), rc=0)
    assert oc.state == SUCCEEDED and oc.summary is None and oc.code is None


def test_b15_cancelled_jobs_do_not_name_failures():
    class Cancelled(_Job):
        class cancel:  # noqa: N801 — `job.cancel.by` 만 읽는다
            by = "alice"

    oc = outcome_for(
        Cancelled(),
        markers(("step", "test"), ("fail", "x_test.dart")),
        started=T0,
        finished=T0 + timedelta(minutes=1),
        rc=-15,
        cancelled=True,
    )
    assert oc.summary == "cancelled by alice" and oc.code == "cancelled_by"


# ── 두 완료 자리가 같은 글자를 낸다 (B7) ──────────────────────────────────────


def _local(tmp_path) -> dict:
    """로컬 레인으로 실제로 돌린 잡의 요약 셋."""
    cfg = make_config(tmp_path / "local")
    script = "".join(f"echo '{line}'; " for line in GATE_LOG.splitlines()) + "exit 1"
    cfg.presets = (*cfg.presets, parse_preset(sh("gate-names", script)))
    store = Store(cfg.data_dir / "rcm.sqlite3")
    try:
        jid = enqueue(store, cfg, "gate-names")
        run_one(store, cfg, jid)
        j = store.get_job(jid)
        return {"summary": j.summary, "code": j.summary_code, "args": dict(j.summary_args)}
    finally:
        store.close()


def _remote(tmp_path) -> dict:
    """원격 워커가 같은 로그를 올리고 finish 한 잡의 요약 셋."""
    srv = WorkerServer(tmp_path / "remote")
    try:
        jid = srv.queued_job()
        srv.registered("build-02")
        assert srv.claimed("build-02") == jid
        assert srv.log("build-02", jid, GATE_LOG.encode())[0] == 200
        assert srv.finish("build-02", jid, "failed", exit_code=1)[0] == 200
        v = srv.view(jid)
        return {"summary": v["summary"], "code": v["summary_code"], "args": v["summary_args"]}
    finally:
        srv.close()


def test_b7_local_lane_and_remote_worker_write_the_same_summary(tmp_path):
    local = _local(tmp_path)
    remote = _remote(tmp_path)
    assert local == {"summary": GATE_TEXT, "code": "exit_code", "args": GATE_ARGS}
    assert remote == local


# ── rcm top 의 최근 줄: 같은 스텝을 두 번 말하지 않는다 (웹 W4 와 같은 규칙) ──────


def test_cli_recent_line_does_not_repeat_the_step_the_summary_names():
    from test_render_m5h import recent_row, tail_of

    row = recent_row(
        failed_step="test",
        last_step="test",
        summary=GATE_TEXT,
        summary_code="exit_code",
        summary_args=GATE_ARGS,
    )
    assert tail_of(row) == GATE_TEXT
    old = recent_row(failed_step="test", summary="exit 1", summary_code="exit_code")
    assert tail_of({**old, "summary_args": {"code": 1}}) == "exit 1 (step test)"  # 옛 행은 그대로
    timed = recent_row(
        state="timed_out",
        exit_code=-9,
        last_step="test",
        summary="limit 1m (in test)",
        summary_code="timed_out",
        summary_args={"seconds": 60, "last_step": "test"},
    )
    assert tail_of(timed) == "limit 1m (in test)"


# ── B8 (검증 라운드 1): C1 제어 문자 · 줄 구분자가 대장 · 요약 · env 로 새지 않는다 ─────────

#: 검증자가 넣은 이름 — NEL(U+0085) · LINE SEPARATOR(U+2028) · RLO(U+202E) · ESC 를 한 줄에
EVIL = "a\x85b c‮evil\x1b[31mred"
#: 이 문자들 가운데 하나라도 남으면 터미널이나 `splitlines()` 가 줄을 나눈다
LINE_BREAKERS = ("\x85", " ", " ", "\x1c", "\x1d", "\x1e", "\x0b", "\x0c", "\r", "\n")


def _no_breaks(text: str) -> bool:
    return not any(ch in text for ch in LINE_BREAKERS) and "\x1b" not in text


@pytest.mark.parametrize("ch", ["\x85", "\x9b", " ", " ", "؜"])
def test_b8_the_marker_entry_drops_c1_and_line_separators(ch):
    """입구(`progress.clean_name`)에서 지우면 대장 · `failed_step` · env · 요약이 다 덮인다."""
    from remote_ci_monitor.core.progress import clean_name, parse_marker

    assert clean_name(f"x{ch}y") == "xy"
    assert parse_marker(f"::rcm::fail::x{ch}y\n") == ("fail", "xy")
    assert parse_marker(f"::rcm::step::te{ch}st\n") == ("step", "test")


def test_b8_outcome_drops_c1_even_if_a_name_skipped_the_entry():
    """방어 한 겹 더 — 옛 대장 행처럼 입구를 안 지난 이름도 요약 인자에서 지워진다."""
    _, _, args = outcome.exit_summary(1, "te\x85st", ["a\x85b c_test.dart"], ["te\x85st"])
    assert args["first"] == "abc_test.dart" and args["step"] == "test"


def test_b8_hook_lines_match_the_count_under_splitlines():
    from remote_ci_monitor.core.notify import notify_env

    env = notify_env({"id": 1}, "hook", failures=["a\x85b c d؜e", "second"])
    assert env["RCM_FAILURES"] == "abcde\nsecond"
    assert len(env["RCM_FAILURES"].splitlines()) == int(env["RCM_FAILURE_COUNT"]) == 2


def _evil_log(second: str = "second") -> str:
    return f"::rcm::step::test\n::rcm::fail::{EVIL}\n::rcm::fail::{second}\n::rcm::fail::test\n"


def _check_evil(summary: str, args: dict, names: list[str]) -> None:
    assert summary == "test: abcevil[31mred +1 more (exit 1)", repr(summary)
    assert args["first"] == "abcevil[31mred"
    assert all(_no_breaks(n) for n in names), names
    assert names[0] == "abc‮evil[31mred"  # 대장은 이름을 지킨다 — 줄을 나누는 글자만 뺀다
    from remote_ci_monitor.core.notify import notify_env

    env = notify_env({"id": 1}, "hook", failures=names)
    assert len(env["RCM_FAILURES"].splitlines()) == int(env["RCM_FAILURE_COUNT"]) == 3


def test_b8_local_lane_keeps_c1_out_of_ledger_summary_and_env(tmp_path):
    cfg = make_config(tmp_path)
    log = tmp_path / "evil.log"
    log.write_text(_evil_log(), encoding="utf-8")
    cfg.presets = (*cfg.presets, parse_preset(sh("evil", f"cat '{log}'; exit 1")))
    store = Store(cfg.data_dir / "rcm.sqlite3")
    try:
        jid = enqueue(store, cfg, "evil")
        run_one(store, cfg, jid)
        j = store.get_job(jid)
        _check_evil(j.summary, j.summary_args, store.failure_names(jid))
        assert j.failed_step == "test"
    finally:
        store.close()


def test_b8_remote_finish_keeps_c1_out_of_ledger_summary_and_env(tmp_path):
    srv = WorkerServer(tmp_path)
    try:
        jid = srv.queued_job()
        srv.registered("build-02")
        assert srv.claimed("build-02") == jid
        assert srv.log("build-02", jid, _evil_log().encode())[0] == 200
        assert srv.finish("build-02", jid, "failed", exit_code=1)[0] == 200
        v = srv.view(jid)
        _check_evil(v["summary"], v["summary_args"], srv.store.failure_names(jid))
        assert all(_no_breaks(f["name"]) for f in v["failures"])
    finally:
        srv.close()


# ── 꼬리 라벨 비교는 씻은 이름끼리 (검증 라운드 1, nice) ───────────────────────


@pytest.mark.parametrize("step", ["s" * 120, "te​st", "te\x85st"])
def test_cli_recent_line_compares_the_cleaned_step(step):
    from test_render_m5h import recent_row, tail_of

    text, code, args = outcome.exit_summary(1, step, [step], [step])
    row = recent_row(failed_step=step, summary=text, summary_code=code, summary_args=args)
    assert tail_of(row) == text  # `(step sss…)` 를 다시 붙이지 않는다
