"""알림 순수 규칙(M5a-3) — `rules_for` 필터·순서 · `notify_env` 키 완전성 · 사용자 문자열 정화
(NUL · 제어문자 · 4096 바이트 UTF-8 안전 절단 · 개행 보존 · None → "").

순수 `core/notify.py` 만 본다. 스레드 · `notifications` 테이블 claim · argv 실행 · URL POST · 설정
검증(`[[notify]]`)은 B/C 의 몫이다.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from remote_ci_monitor.core.notify import NotifyRule, notify_env, rules_for, sanitize_text

TERMINAL = ("succeeded", "failed", "timed_out", "cancelled", "lost")
LIMIT = 4096


def rule(
    name: str,
    on: tuple[str, ...] = TERMINAL,
    presets: tuple[str, ...] | None = None,
    argv: tuple[str, ...] | None = ("bash", "/opt/rcm/notify.sh"),
    url: str | None = None,
    timeout: int = 30,
) -> NotifyRule:
    return NotifyRule(
        name=name,
        on=frozenset(on),
        presets=None if presets is None else frozenset(presets),
        argv=None if argv is None else tuple(argv),
        url=url,
        timeout_seconds=timeout,
    )


SLACK_FAIL = rule("slack-fail", on=("failed", "timed_out", "lost"), presets=("gate", "deploy"))
ALL_DONE = rule("all-done")  # 모든 종료 상태 · 모든 프리셋
QA_OK = rule("qa-ok", on=("succeeded",), presets=("qa",), argv=None, url="https://hooks.example/q")
RULES = (SLACK_FAIL, ALL_DONE, QA_OK)

ROW: dict[str, Any] = {
    "id": 412,
    "preset": "gate",
    "key": "gate:full",
    "inputs": {"scope": "full"},
    "requester": {"name": "alice-laptop", "label": "alice@laptop"},
    "joiners": [],
    "state": "failed",
    "exit_code": 1,
    "job_seconds": 412.5,
    "waited_seconds": 80.0,
    "summary": "3 tests failed",
    "failed_step": "test",
    "url": "http://build:8787/#/jobs/412",
}
ENV_KEYS = {
    "RCM_JOB_ID",
    "RCM_STATE",
    "RCM_PRESET",
    "RCM_KEY",
    "RCM_REQUESTER",
    "RCM_SUMMARY",
    "RCM_FAILED_STEP",
    "RCM_EXIT_CODE",
    "RCM_JOB_SECONDS",
    "RCM_URL",
    "RCM_NOTIFY",
}


def utf8_len(s: str) -> int:
    return len(s.encode("utf-8", "surrogateescape"))


# ── NotifyRule ───────────────────────────────────────────────────────────────


def test_rule_is_frozen_with_the_declared_fields() -> None:
    assert SLACK_FAIL.name == "slack-fail"
    assert SLACK_FAIL.on == frozenset({"failed", "timed_out", "lost"})
    assert SLACK_FAIL.presets == frozenset({"gate", "deploy"})
    assert SLACK_FAIL.argv == ("bash", "/opt/rcm/notify.sh") and SLACK_FAIL.url is None
    assert SLACK_FAIL.timeout_seconds == 30
    assert QA_OK.argv is None and QA_OK.url == "https://hooks.example/q"
    with pytest.raises(dataclasses.FrozenInstanceError):
        SLACK_FAIL.name = "x"  # type: ignore[misc]


# ── rules_for ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("state", "preset", "want"),
    [
        ("failed", "gate", [SLACK_FAIL, ALL_DONE]),
        ("failed", "deploy", [SLACK_FAIL, ALL_DONE]),
        ("timed_out", "gate", [SLACK_FAIL, ALL_DONE]),
        ("lost", "gate", [SLACK_FAIL, ALL_DONE]),
        ("failed", "qa", [ALL_DONE]),  # presets 에 없으면 그 규칙만 빠진다
        ("succeeded", "gate", [ALL_DONE]),  # on 에 없으면 빠진다
        ("cancelled", "gate", [ALL_DONE]),
        ("succeeded", "qa", [ALL_DONE, QA_OK]),
        ("failed", "gate-fast", [ALL_DONE]),  # 접두가 아니라 정확한 이름
        ("running", "gate", []),  # 종료 상태가 아니면 아무 규칙도 안 맞는다
        ("queued", "qa", []),
    ],
)
def test_rules_for_filters_by_state_and_preset(
    state: str, preset: str, want: list[NotifyRule]
) -> None:
    assert rules_for(state, preset, RULES) == want


def test_rules_for_preserves_config_order() -> None:
    assert rules_for("failed", "gate", (SLACK_FAIL, ALL_DONE)) == [SLACK_FAIL, ALL_DONE]
    assert rules_for("failed", "gate", (ALL_DONE, SLACK_FAIL)) == [ALL_DONE, SLACK_FAIL]


def test_rules_for_with_no_rules_or_no_match_is_empty() -> None:
    assert rules_for("failed", "gate", ()) == []
    assert rules_for("failed", "gate", [rule("never", on=("succeeded",))]) == []


def test_rules_for_accepts_any_sequence_and_returns_a_new_list() -> None:
    got = rules_for("lost", "deploy", list(RULES))
    assert got == [SLACK_FAIL, ALL_DONE] and got is not RULES


# ── notify_env: 키 · 값 ──────────────────────────────────────────────────────


def test_env_has_exactly_the_documented_keys_all_strings() -> None:
    env = notify_env(ROW, "slack-fail")
    assert set(env) == ENV_KEYS
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in env.items())


def test_env_values_come_from_the_row_and_the_rule_name() -> None:
    env = notify_env(ROW, "slack-fail")
    assert env["RCM_JOB_ID"] == "412"
    assert env["RCM_STATE"] == "failed"
    assert env["RCM_PRESET"] == "gate"
    assert env["RCM_KEY"] == "gate:full"
    assert env["RCM_REQUESTER"] == "alice@laptop"  # 워커의 RCM_REQUESTER 와 같은 label
    assert env["RCM_SUMMARY"] == "3 tests failed"
    assert env["RCM_FAILED_STEP"] == "test"
    assert env["RCM_EXIT_CODE"] == "1"
    assert float(env["RCM_JOB_SECONDS"]) == 412.5
    assert env["RCM_URL"] == "http://build:8787/#/jobs/412"
    assert env["RCM_NOTIFY"] == "slack-fail"


def test_env_none_becomes_empty_string() -> None:
    row = {
        **ROW,
        "state": "lost",
        "exit_code": None,
        "job_seconds": None,
        "summary": None,
        "failed_step": None,
        "url": None,
    }
    env = notify_env(row, "all-done")
    assert env["RCM_EXIT_CODE"] == ""
    assert env["RCM_JOB_SECONDS"] == ""
    assert env["RCM_SUMMARY"] == ""
    assert env["RCM_FAILED_STEP"] == ""
    assert env["RCM_URL"] == ""
    assert env["RCM_STATE"] == "lost" and env["RCM_NOTIFY"] == "all-done"


def test_env_exit_code_zero_is_the_string_zero_not_empty() -> None:
    env = notify_env({**ROW, "state": "succeeded", "exit_code": 0, "job_seconds": 0.0}, "x")
    assert env["RCM_EXIT_CODE"] == "0"
    assert float(env["RCM_JOB_SECONDS"]) == 0.0


def test_env_missing_optional_row_keys_become_empty_strings() -> None:
    minimal = {
        "id": 7,
        "state": "lost",
        "preset": "gate",
        "key": "gate",
        "requester": {"name": "bob-desk", "label": "bob@desk"},
    }
    env = notify_env(minimal, "all-done")
    assert set(env) == ENV_KEYS
    assert env["RCM_JOB_ID"] == "7" and env["RCM_REQUESTER"] == "bob@desk"
    assert env["RCM_SUMMARY"] == env["RCM_FAILED_STEP"] == env["RCM_URL"] == ""
    assert env["RCM_EXIT_CODE"] == env["RCM_JOB_SECONDS"] == ""


def test_env_does_not_mutate_the_row() -> None:
    row = {**ROW, "summary": "a\x00b"}
    before = dict(row)
    notify_env(row, "x")
    assert row == before


# ── notify_env: 사용자 문자열 정화 ───────────────────────────────────────────


def test_env_strips_nul_and_escape_from_user_strings() -> None:
    row = {
        **ROW,
        "summary": "boom\x00 \x1b[31mred\x1b[0m",
        "failed_step": "te\x00st\x07",
        "requester": {"name": "alice-laptop", "label": "ali\x00ce@lap\x1btop"},
    }
    env = notify_env(row, "x")
    assert env["RCM_SUMMARY"] == "boom [31mred[0m"
    assert env["RCM_FAILED_STEP"] == "test"
    assert env["RCM_REQUESTER"] == "alice@laptop"


def test_env_keeps_newlines_in_summary() -> None:
    env = notify_env({**ROW, "summary": "line1\nline2\n\ttabbed"}, "x")
    assert env["RCM_SUMMARY"] == "line1\nline2\n\ttabbed"


def test_env_truncates_long_summary_to_4096_bytes_on_a_char_boundary() -> None:
    env = notify_env({**ROW, "summary": "가" * 2000, "failed_step": "x" * 5000}, "x")
    assert env["RCM_SUMMARY"] == "가" * 1365  # 4095 바이트 — 3 바이트 문자를 자르지 않는다
    assert env["RCM_FAILED_STEP"] == "x" * 4096
    for v in env.values():
        assert utf8_len(v) <= LIMIT
        v.encode("utf-8")  # 깨진 서로게이트 없음


# ── sanitize_text ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("", ""),
        ("plain", "plain"),
        ("a\x00b", "ab"),
        ("\x00\x00", ""),
        ("\x1b[31mred\x1b[0m", "[31mred[0m"),
        ("a\x07b\x08c", "abc"),
        ("a\x7fb", "ab"),  # DEL 도 제어문자
        ("a\r\nb", "a\nb"),  # CR 은 빠지고 LF 는 남는다
        ("a\nb", "a\nb"),
        ("a\tb", "a\tb"),
        ("\n\t\n", "\n\t\n"),
        ("한글 テスト ✓ 🚀", "한글 テスト ✓ 🚀"),
        ("\x01\x02\x1f x \x1f", " x "),
    ],
)
def test_sanitize_text_removes_control_chars_except_newline_and_tab(raw: str, want: str) -> None:
    assert sanitize_text(raw) == want


def test_sanitize_text_keeps_exactly_4096_bytes() -> None:
    assert sanitize_text("a" * LIMIT) == "a" * LIMIT
    assert sanitize_text("가" * 1365) == "가" * 1365  # 4095
    assert sanitize_text("🚀" * 1024) == "🚀" * 1024  # 정확히 4096


def test_sanitize_text_truncates_ascii_at_4096_bytes() -> None:
    assert sanitize_text("a" * 5000) == "a" * LIMIT


def test_sanitize_text_truncates_three_byte_chars_on_a_boundary() -> None:
    got = sanitize_text("가" * 2000)  # 6000 바이트
    assert got == "가" * 1365  # 4095 바이트 — 1366 개면 4098
    assert utf8_len(got) == 4095


def test_sanitize_text_truncates_four_byte_chars_on_a_boundary() -> None:
    got = sanitize_text("🚀" * 1100)  # 4400 바이트
    assert got == "🚀" * 1024 and utf8_len(got) == LIMIT


def test_sanitize_text_drops_a_char_that_would_straddle_the_limit() -> None:
    assert sanitize_text("a" * 4095 + "가") == "a" * 4095
    assert sanitize_text("a" * 4094 + "가") == "a" * 4094  # 4094 + 3 = 4097 → 문자 통째로 뺀다
    assert sanitize_text("a" * 4093 + "가") == "a" * 4093 + "가"  # 4096 딱 맞으면 남는다


def test_sanitize_text_removes_control_chars_before_truncating() -> None:
    # NUL 을 먼저 빼고 자른다 — 자른 뒤 빼면 4086 글자만 남는다
    assert sanitize_text("\x00" * 10 + "a" * LIMIT) == "a" * LIMIT


def test_sanitize_text_keeps_newlines_when_truncating() -> None:
    got = sanitize_text("line\n" * 1000)  # 5000 바이트
    assert got == ("line\n" * 819) + "l"  # 4095 + 1
    assert got.count("\n") == 819


def test_sanitize_text_limit_parameter() -> None:
    assert sanitize_text("abcdef", limit=3) == "abc"
    assert sanitize_text("가나다", limit=4) == "가"
    assert sanitize_text("가나다", limit=6) == "가나"
    assert sanitize_text("abc", limit=0) == ""


def test_sanitize_text_survives_lone_surrogates() -> None:
    # surrogateescape 로 들어온 깨진 바이트가 섞여 있어도 예외 없이 한도 안의 str 을 돌려준다
    got = sanitize_text("a\udcffb" + "x" * 5000)
    assert isinstance(got, str) and utf8_len(got) <= LIMIT
    assert got.startswith("a")


def test_sanitize_text_is_identity_on_clean_short_text() -> None:
    s = "ok: 12 passed in 3.2s\n"
    assert sanitize_text(s) == s
