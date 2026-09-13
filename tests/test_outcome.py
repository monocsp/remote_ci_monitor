"""요약 코드 표(M5d-0, 오너 결정 37) — `core/outcome.py` 의 순수 계층.

이 파일의 요점은 **글자 하나까지 같은 영어 문장**이다. 지금 서버 곳곳에서 만들어 내는 문장을 한
표로 옮기는 작업이라, 표를 거쳐도 읽는 사람이 보는 글자가 달라지면 안 된다. 그래서 문장을 상수로
박아 두고(`PINNED`), 서버에 아직 남아 있는 원본 상수·포맷 함수와도 맞대어 본다.

인자가 빠져도 던지지 않는다 — 요약 한 줄 때문에 잡이 깨지면 안 되기 때문이다. 모르는 코드는
`None` 으로 물러서서, 보여 주는 쪽이 저장된 `summary` 문장을 그대로 쓸 수 있게 한다.
명세는 docs/m5d-workplan.md §4.5. 구현 전이라 빨간 것이 정상이다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from remote_ci_monitor.core.outcome import (
    CODES,
    ERROR_CODES,
    GPU_NOTE_CODES,
    MAX_SUMMARY,
    OutcomeError,
    dump_args,
    load_args,
    render,
    summary,
)
from remote_ci_monitor.remote_workers import (
    SUMMARY_CANCEL_UNCONFIRMED,
    SUMMARY_RESTARTED,
    SUMMARY_UNREACHABLE,
)
from remote_ci_monitor.server import _mb as server_mb
from remote_ci_monitor.worker import format_limit

#: 코드마다 「그럴듯한」 인자. `CODES` 와 키 집합이 같은지 검사하므로, 코드를 더하면 여기도 는다.
PLAUSIBLE: dict[str, dict[str, Any]] = {
    "cancelled_before_start": {},
    "cancelled_by": {"by": "alice@laptop"},
    "server_restarted": {"at": "2026-09-08 01:02:03Z"},
    "server_restarted_during_upload": {},
    "server_stopped_while_running": {},
    "upload_abandoned": {"seconds": 300},
    "upload_interrupted": {"bytes": 30_000_000},
    "snapshot_too_big": {"bytes": 700_000_000, "limit": 512_000_000},
    "snapshot_rejected": {"kind": "TarError"},
    "snapshot_blobs_missing": {"count": 3},
    "workspace_failed": {"detail": "git checkout failed"},
    "exit_code": {"code": 1},
    "timed_out": {"seconds": 1200},
    "tool_missing": {"tool": "fvm"},
    "worker_error": {"detail": "boom"},
    "worker_failed": {},
    "worker_stopped_while_running": {},
    "worker_restarted_without_job": {"name": "build-02"},
    "worker_unreachable": {"name": "build-02", "seconds": 61},
    "cancel_unconfirmed": {},
}

#: 지금 서버가 만드는 문장 전수. **여기를 고치려면 사용자에게 보이는 글자가 바뀐다는 뜻이다.**
PINNED: list[tuple[str, dict[str, Any], str]] = [
    ("cancelled_before_start", {}, "cancelled before start"),
    ("cancelled_by", {"by": "alice@laptop"}, "cancelled by alice@laptop"),
    (
        "server_restarted",
        {"at": "2026-09-08 01:02:03Z"},
        "server restarted 2026-09-08 01:02:03Z",
    ),
    ("server_restarted_during_upload", {}, "server restarted during upload"),
    ("server_stopped_while_running", {}, "server stopped while running"),
    ("upload_abandoned", {"seconds": 300}, "upload abandoned after 5m"),
    ("upload_abandoned", {"seconds": 45}, "upload abandoned after 45s"),
    ("upload_interrupted", {"bytes": 30_000_000}, "upload interrupted after 30 MB"),
    (
        "snapshot_too_big",
        {"bytes": 700_000_000, "limit": 512_000_000},
        "snapshot 700 MB exceeds 512 MB",
    ),
    ("snapshot_rejected", {"kind": "TarError"}, "snapshot rejected: TarError"),
    ("snapshot_blobs_missing", {"count": 3}, "snapshot rejected: 3 blob(s) missing in upload"),
    ("exit_code", {"code": 1}, "exit 1"),
    ("timed_out", {"seconds": 1200}, "limit 20m"),
    ("timed_out", {"seconds": 3600}, "limit 1h"),
    ("timed_out", {"seconds": 90}, "limit 90s"),
    ("worker_error", {"detail": "boom"}, "worker error: boom"),
    ("worker_failed", {}, "failed on the worker"),
    ("worker_stopped_while_running", {}, "worker stopped while running"),
    (
        "worker_restarted_without_job",
        {"name": "build-02"},
        "worker build-02 restarted without the job",
    ),
    (
        "worker_unreachable",
        {"name": "build-02", "seconds": 61},
        "worker build-02 unreachable for 61s",
    ),
    ("cancel_unconfirmed", {}, "worker did not confirm the cancel"),
]


# ── 표 자체 ──────────────────────────────────────────────────────────────────


def test_plausible_args_cover_every_code():
    """코드를 더하면서 테스트를 안 고치는 일을 막는다."""
    assert set(PLAUSIBLE) == set(CODES)
    assert {code for code, _, _ in PINNED} <= set(CODES)


@pytest.mark.parametrize("code", sorted(CODES))
def test_every_code_renders_a_non_empty_english_sentence(code):
    text = render(code, PLAUSIBLE[code])
    assert text, code
    assert text.isascii(), text  # 서버가 만드는 문장은 영어다 — 번역은 보여 주는 쪽 몫
    assert text == text.strip() and "None" not in text
    assert len(text) <= MAX_SUMMARY


@pytest.mark.parametrize("code", sorted(CODES))
def test_every_code_survives_missing_args(code):
    """인자가 빠져도 던지지 않는다 — 요약 한 줄 때문에 잡이 깨지면 안 된다."""
    for args in ({}, None, {"unexpected": "junk"}):
        text = render(code, args)
        assert text, (code, args)
        assert "None" not in text  # 빠진 값은 `?` 로 — 그대로 `None` 을 찍지 않는다


# ── render() 의 물러서기 ─────────────────────────────────────────────────────


def test_render_returns_none_for_unknown_and_missing_code():
    """모르는 코드는 `None` — 부르는 쪽이 저장된 문장으로 물러설 수 있어야 한다."""
    assert render(None) is None
    assert render(None, {"by": "alice"}) is None
    assert render("") is None
    assert render("no_such_code") is None
    assert render("no_such_code", {"by": "alice"}) is None


# ── summary() ────────────────────────────────────────────────────────────────


def test_summary_rejects_an_unknown_code():
    """서버 안의 오타를 즉시 잡는다(밖에서 들어오는 값이 아니다)."""
    with pytest.raises(OutcomeError):
        summary("unknown_code")
    assert issubclass(OutcomeError, ValueError)


def test_summary_returns_text_code_args_and_drops_none():
    text, code, args = summary("worker_unreachable", name="build-02", seconds=61, detail=None)
    assert text == "worker build-02 unreachable for 61s"
    assert code == "worker_unreachable"
    assert args == {"name": "build-02", "seconds": 61}  # None 인자는 저장하지 않는다
    assert summary("worker_failed") == ("failed on the worker", "worker_failed", {})
    assert summary("exit_code", code=0)[2] == {"code": 0}  # 0 은 None 이 아니다 — 남는다


def test_summary_text_equals_render_for_every_code():
    """저장할 문장과 나중에 코드로 다시 그린 문장이 같다 — 화면·CLI 가 갈라지지 않는다."""
    for code, args, expected in PINNED:
        text, got_code, got_args = summary(code, **args)
        assert text == expected
        assert render(got_code, got_args) == expected


# ── 지금 서버가 쓰는 글자 그대로 ─────────────────────────────────────────────


@pytest.mark.parametrize(("code", "args", "expected"), PINNED, ids=[p[2] for p in PINNED])
def test_rendered_text_matches_todays_server_text(code, args, expected):
    assert render(code, args) == expected


def test_worker_summaries_match_the_constants_still_in_the_server():
    """`remote_workers` 의 상수와 표가 갈라지면 여기서 걸린다."""
    assert render("worker_restarted_without_job", {"name": "build-02"}) == SUMMARY_RESTARTED.format(
        name="build-02"
    )
    assert render(
        "worker_unreachable", {"name": "build-02", "seconds": 61}
    ) == SUMMARY_UNREACHABLE.format(name="build-02", seconds=61)
    assert render("cancel_unconfirmed", {}) == SUMMARY_CANCEL_UNCONFIRMED


@pytest.mark.parametrize("seconds", [None, 0, 1, 59, 60, 90, 1200, 3599, 3600, 7200])
def test_timeout_sentence_matches_the_workers_format_limit(seconds):
    """시간 초과 문구는 `worker.format_limit` 이 원본이다 — 표가 그 글자를 그대로 낸다."""
    assert render("timed_out", {"seconds": seconds}) == format_limit(seconds)
    assert render("timed_out", {}) == format_limit(None) == "limit"


# ── 크기·시간 표기 ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("n", "shown"),
    [
        (0, "0 KB"),
        (1, "0 KB"),
        (999_499, "999 KB"),
        (999_999, "1000 KB"),  # 1e6 미만은 KB — 반올림해서 1000 KB 가 되어도 그대로다
        (1_000_000, "1 MB"),
        (1_500_000, "2 MB"),
        (30_000_000, "30 MB"),
        (512_000_000, "512 MB"),
    ],
)
def test_bytes_use_kb_under_one_megabyte_and_mb_at_or_above(n, shown):
    assert render("upload_interrupted", {"bytes": n}) == f"upload interrupted after {shown}"
    assert shown == server_mb(n)  # 서버의 기존 `_mb` 와 글자 하나까지 같다


def test_bytes_formatting_never_raises_on_junk():
    assert render("upload_interrupted", {"bytes": "not a number"}) == ("upload interrupted after ?")
    assert render("upload_interrupted", {}) == "upload interrupted after ?"


@pytest.mark.parametrize(
    ("seconds", "shown"),
    [(0, "0s"), (1, "1s"), (59, "59s"), (60, "1m"), (90, "1m"), (300, "5m"), (3600, "60m")],
)
def test_seconds_use_seconds_under_a_minute_and_whole_minutes_above(seconds, shown):
    assert render("upload_abandoned", {"seconds": seconds}) == f"upload abandoned after {shown}"


def test_seconds_accepts_floats_like_the_config_value():
    """`upload_abandon_seconds` 는 실수다 — 300.0 도 `5m` 이다."""
    assert render("upload_abandoned", {"seconds": 300.0}) == "upload abandoned after 5m"
    assert render("upload_abandoned", {"seconds": 45.9}) == "upload abandoned after 45s"
    assert render("upload_abandoned", {"seconds": None}) == "upload abandoned after ?"


# ── 길이 상한 ────────────────────────────────────────────────────────────────


def test_long_text_is_truncated_to_max_summary():
    long = "x" * 400
    text = render("worker_error", {"detail": long})
    assert len(text) == MAX_SUMMARY == 200
    assert text == f"worker error: {long}"[:MAX_SUMMARY]
    assert summary("worker_error", detail=long)[0] == text
    assert len(render("workspace_failed", {"detail": long})) == MAX_SUMMARY


# ── 인자 직렬화 ──────────────────────────────────────────────────────────────


def test_dump_and_load_args_round_trip():
    args = {"name": "build-02", "seconds": 61, "bytes": 30_000_000, "ok": False}
    raw = dump_args(args)
    assert isinstance(raw, str) and load_args(raw) == args
    assert json.loads(raw) == args


def test_dump_args_is_none_when_there_is_nothing_to_say():
    assert dump_args(None) is None
    assert dump_args({}) is None


def test_dump_args_stringifies_what_json_cannot_hold():
    """`default=str` 이라 datetime 같은 값도 잡을 깨뜨리지 않는다."""
    raw = dump_args({"at": datetime(2026, 9, 8, 1, 2, 3, tzinfo=UTC)})
    assert raw is not None and load_args(raw)["at"].startswith("2026-09-08")


def test_load_args_returns_an_empty_dict_for_anything_that_is_not_an_object():
    assert load_args(None) == {}
    assert load_args("") == {}
    assert load_args("{not json") == {}
    assert load_args("[1, 2, 3]") == {}  # 목록은 인자가 아니다
    assert load_args('"just a string"') == {}
    assert load_args("17") == {}
    assert load_args({"already": "a dict"}) == {"already": "a dict"}


# ── 오류·GPU 코드 목록 ───────────────────────────────────────────────────────


def test_error_and_gpu_note_code_tables_are_closed_sets_of_ascii_text():
    """화면이 자기 말로 쓰려면 코드가 닫힌 집합이어야 한다(원문은 상세로 따로 남는다)."""
    assert "internal_error" in ERROR_CODES  # 분류가 안 되면 여기로 떨어진다
    assert {"database_unavailable", "sampler_failed"} <= set(ERROR_CODES)
    assert set(GPU_NOTE_CODES) == {"no_sampler", "no_gpu", "sampler_failed"}
    for table in (ERROR_CODES, GPU_NOTE_CODES):
        for code, text in table.items():
            assert code.isascii() and code.islower() and " " not in code
            assert text and text.isascii()
