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
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from remote_ci_monitor.core.outcome import (
    CODES,
    ERROR_CODES,
    GPU_NOTE_CODES,
    MAX_SUMMARY,
    PREFLIGHT_ARGS,
    PREFLIGHT_CODES,
    PREFLIGHT_REQUIRED,
    REJECT_KINDS,
    OutcomeError,
    clean_args,
    dump_args,
    load_args,
    render,
    summary,
)
from remote_ci_monitor.gitops import _fmt_seconds
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
    "snapshot_rejected": {"kind": "escapes_workspace", "member": "escape.txt"},
    "snapshot_blobs_missing": {"count": 3},
    "exit_code": {"code": 1},
    "timed_out": {"seconds": 1200},
    # ── 시작 전에 끝난 잡(F2b) ────────────────────────────────────────────
    "tool_missing": {"tool": "fvm"},
    "preset_missing": {"preset": "gate"},
    "snapshot_missing": {},
    "blob_missing": {"sha": "1234567"},
    "repo_missing": {"repo": "app"},
    "commit_missing": {"sha": "1234567"},
    "git_failed": {"op": "git fetch", "kind": "exit", "code": 128},
    "snapshot_download_failed": {"status": 503},
    "launch_executable_missing": {},
    "launch_permission_denied": {},
    "launch_failed": {"error": "OSError"},
    "log_unavailable": {"error": "PermissionError"},
    "workspace_failed": {"error": "OSError"},
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
    (
        "snapshot_rejected",
        {"kind": "escapes_workspace", "member": "escape.txt"},
        "snapshot rejected: member escapes the workspace: escape.txt",
    ),
    ("snapshot_rejected", {"kind": "not_a_tarball"}, "snapshot rejected: not a valid tar.gz"),
    ("snapshot_blobs_missing", {"count": 3}, "snapshot rejected: 3 blob(s) missing in upload"),
    ("exit_code", {"code": 1}, "exit 1"),
    # ── 시작 전에 끝난 잡(F2b) ────────────────────────────────────────────────
    # 여기 문장은 코드가 생기기 **전** 워커가 쓰던 문장과 글자까지 같은 것이 많다 — 사용자에게
    # 보이는 글자를 바꾸지 않고 기계가 읽을 사실만 더한 것이 이 작업이다.
    ("preset_missing", {"preset": "ok"}, "preset 'ok' is no longer configured"),
    ("tool_missing", {"tool": "fvm"}, "required tool fvm is missing"),
    ("snapshot_missing", {}, "snapshot file is missing"),
    ("blob_missing", {"sha": "1234567"}, "snapshot blob missing 1234567"),
    ("repo_missing", {"repo": "app"}, "repo 'app' is no longer configured"),
    (
        "repo_missing",
        {"repo": "app", "where": "worker"},
        "repo 'app' is not configured on this worker",
    ),
    (
        "commit_missing",
        {"sha": "1234567"},
        "commit 1234567 not found after fetch (ref moved or was force-pushed?)",
    ),
    (
        "git_failed",
        {"op": "git fetch", "kind": "timeout", "seconds": 1},
        "git fetch timed out after 1s",
    ),
    (
        "git_failed",
        {"op": "git fetch", "kind": "exit", "code": 128},
        "git fetch failed (exit 128), see the job log",
    ),
    ("git_failed", {"kind": "no_git"}, "git is not installed on the build machine"),
    (
        "git_failed",
        {"op": "git init", "kind": "spawn", "error": "OSError"},
        "git init could not start git: OSError",
    ),
    (
        "snapshot_download_failed",
        {"status": 503},
        "cannot download the snapshot from the server (HTTP 503)",
    ),
    ("launch_executable_missing", {}, "the preset's command was not found"),
    ("launch_permission_denied", {}, "the preset's command is not executable"),
    (
        "launch_failed",
        {"error": "OSError"},
        "the preset's command could not be started (OSError)",
    ),
    (
        "log_unavailable",
        {"error": "PermissionError"},
        "the job log file could not be opened (PermissionError)",
    ),
    (
        "workspace_failed",
        {"error": "OSError"},
        "the workspace could not be prepared (OSError)",
    ),
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


def test_pinned_sentences_cover_every_code_and_the_table_is_not_empty():
    """`PINNED` 를 「문장 전수」라고 부르려면 전수여야 한다. 부분집합(`<=`)만 검사하면 코드를
    더하면서 문장을 안 박아도 초록이고, 표를 통째로 비워도(`PINNED = []`) 이 표를 도는 시험은
    0회 성공으로 초록이다 — 둘 다 이 파일에서 실제로 일어난 일이다(검토 9)."""
    assert PINNED, "문장 표가 비었다 — 아래 시험들이 아무것도 검사하지 않는다"
    assert {code for code, _, _ in PINNED} == set(CODES)


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


# ── 시작 전 실패의 닫힌 어휘 (F2b) ──────────────────────────────────────────

#: 공개 문서로 새면 안 되는 것들. 정규식 하나로는 이 중 어느 것도 못 막는다 — 그래서 문구를 씻는
#: 대신 문구를 **안 싣는다**(`PREFLIGHT_ARGS`).
HOSTILE = (
    "/opt/private-sdk/flutter/bin/flutter",
    "~/secrets/runner",
    "$HOME/secrets/runner",
    "\\\\build-secret\\share\\tool",
    "internal-runner-ghp_0123456789abcdef",
    "C:\\Users\\alice\\token.txt",
    "sk-live-0123456789",
)


def test_every_preflight_code_is_a_real_code_with_an_argument_schema():
    """두 표가 갈라지면 한쪽만 늘어난다 — 코드는 있는데 인자 경계가 없거나, 경계만 있고 코드가
    없다. 둘 다 조용하다."""
    assert set(PREFLIGHT_CODES) == set(PREFLIGHT_ARGS)
    assert set(PREFLIGHT_CODES) <= set(CODES)
    assert len(PREFLIGHT_CODES) == len(set(PREFLIGHT_CODES))


#: 「이미 공개된 이름」 인자. 프리셋·레포 이름은 잡 문서에 이미 들어 있고, 도구 이름은 `requires`
#: 가 선언한 것이며, 멤버 이름은 제출자 **자신의** 파일 이름이다. 이것들은 지우는 대신 **모양**을
#: 본다 — 다 지우면 사람이 고칠 수 없다.
NAME_ARGS = {"preset", "repo", "tool", "member"}


@pytest.mark.parametrize("code", sorted(PREFLIGHT_CODES))
def test_no_preflight_code_can_carry_a_server_path_or_a_secret(code):
    """이 결함의 본체다(검토 1). 공개 요약에 자유 문구가 실릴 수 있으면 `argv[0]` · git stderr ·
    예외 문구가 토큰 없이 읽히는 `/api/status` 로 나간다. 경로 정규식을 늘리는 것으로는 못 막는다:
    상대 경로 · `~/` · `$HOME/` · UNC · 토큰 모양은 어떤 식에도 안 걸린다.

    그래서 여기서는 **모든 인자 자리에** 적대적인 값을 넣어 보고, 문장에도 저장할 인자에도
    남지 않는지 본다. 자유 문구 인자를 하나라도 다시 열면 이 시험이 그 자리에서 빨개진다.
    """
    for key in PREFLIGHT_ARGS[code]:
        for bad in HOSTILE:
            text, _, args = summary(code, **{key: bad})
            blob = f"{text} {args}"
            if key in NAME_ARGS:
                # 이름은 남기되 **이름 하나**여야 한다: 경로도, 공백도, 보이지 않는 문자도 아니다.
                kept = args.get(key, "")
                assert not set(kept) & set("/\\ \t"), (code, key, kept)
                assert len(kept) <= 120 and kept == kept.strip()
                continue
            assert bad not in blob, (code, key, blob)
            assert not set(blob) & set("/\\~$"), (code, key, blob)


def test_a_free_text_argument_is_dropped_instead_of_stored():
    """생산자가 실수로 자유 문구를 실어도 표에 없는 키라 버려진다 — 「그냥 저장」이 아니다."""
    text, _, args = summary("workspace_failed", detail="cannot start '/opt/private/x'")
    assert args == {} and "/opt" not in text
    assert summary("tool_missing", tool="/opt/private-sdk/fvm")[2] == {"tool": "fvm"}


@pytest.mark.parametrize(
    ("raw", "kept"),
    [
        ("../escape.txt", "escape.txt"),  # 어느 파일이었는지는 남는다
        ("/generated/two", "two"),  # 절대 멤버도 `<path>` 로 뭉개지 않는다(검토 6)
        ("..\\\\private-host\\secret-share\\f.txt", "f.txt"),  # UNC·역슬래시
        ("a/b/c/deep.txt", "deep.txt"),
        ("we\u202eirdrat.txt", "weirdrat.txt"),  # bidi 는 파일 이름을 거꾸로 보이게 한다
    ],
)
def test_an_archive_member_keeps_its_name_and_nothing_else(raw, kept):
    assert clean_args("snapshot_rejected", {"kind": "escapes_workspace", "member": raw}) == {
        "kind": "escapes_workspace",
        "member": kept,
    }


def test_a_member_name_that_is_not_utf8_never_reaches_the_arguments():
    """`tarfile` 은 멤버 이름을 `surrogateescape` 로 읽는다. 외톨이 서로게이트가 그대로
    `store.finish` 로 가면 SQLite 인코딩이 던지고, 그러면 잡 하나가 아니라 **레인 전체**가
    `down` 이 된다."""
    args = clean_args("snapshot_rejected", {"kind": "truncated", "member": "bad\udcff-name.txt"})
    assert args["member"] == "bad-name.txt"
    assert json.dumps(args).encode("utf-8")  # 다시 인코딩해도 던지지 않는다
    # 이름이 통째로 사라지는 값은 아예 버린다 — `member: ""` 로 저장해 봐야 아무 말도 아니다
    assert clean_args("snapshot_rejected", {"kind": "truncated", "member": "../.."}) == {
        "kind": "truncated"
    }


def test_clean_args_drops_what_it_cannot_shape_and_strict_raises_at_the_boundary():
    """서버 안에서는 버린다(요약 한 줄 때문에 잡이 종료 상태에 못 가면 큐가 막힌다). 원격 워커의
    finish 같은 **신뢰 경계**에서는 아는 키의 값이 틀리면 던진다 — 조용히 반쪽짜리 요약을
    저장하면 로컬 레인이 남기는 행과 달라진다."""
    assert clean_args("git_failed", {"kind": "rm -rf", "op": "sudo rm"}) == {}
    assert clean_args("blob_missing", {"sha": "not-hex"}) == {}
    assert clean_args("repo_missing", {"repo": "app", "where": "anywhere"}) == {"repo": "app"}
    assert clean_args("snapshot_download_failed", {"status": "503"}) == {}
    with pytest.raises(OutcomeError):
        clean_args("git_failed", {"kind": "rm -rf"}, strict=True)
    with pytest.raises(OutcomeError):
        clean_args("not_a_preflight_code", {}, strict=True)


def test_an_unknown_argument_is_dropped_even_at_the_boundary():
    """모르는 키에 400 을 주면 그 자리가 배포 사고가 된다: 새 워커가 옛 서버에 인자를 하나 더
    실어 보내는 순간 finish 가 거절당하고, 잡은 안 닫힌 채 heartbeat 시한으로 `lost` 가 된다.
    버려도 저장되는 값은 표를 지난 것뿐이라 보안 쪽으로는 같다."""
    assert clean_args("tool_missing", {"tool": "fvm", "path": "/etc/shadow"}, strict=True) == {
        "tool": "fvm"
    }


def test_the_required_argument_table_matches_the_sentences():
    """손으로 적은 목록은 생산자를 안 본다 — 문장이 `?` 밖에 못 말하는 코드는 그 인자가 없으면
    안 되는 코드다. 표와 문장이 갈라지면 여기서 걸린다."""
    need_because_of_the_sentence = {c for c in PREFLIGHT_CODES if "?" in (render(c, {}) or "")}
    assert set(PREFLIGHT_REQUIRED) == need_because_of_the_sentence
    for code, key in PREFLIGHT_REQUIRED.items():
        assert key in PREFLIGHT_ARGS[code], (code, key)


def test_reject_kinds_are_closed_lowercase_keys_with_english_phrases():
    """까닭은 **열쇠**로 저장한다 — `OutsideDestinationError` 를 그대로 실으면 파이썬을 아는
    사람만 읽고, 예외 문구를 실으면 그 안에 추출 목적지의 절대 경로가 따라온다."""
    for key, phrase in REJECT_KINDS.items():
        assert key.isascii() and key.islower() and " " not in key
        assert phrase and phrase.isascii() and "/" not in phrase


@pytest.mark.parametrize("seconds", [1, 5, 59, 60, 90, 120, 300, 3599, 3600, 7200])
def test_the_git_timeout_sentence_uses_the_same_words_as_the_job_log(seconds):
    """같은 시간 초과를 잡 로그(`gitops`)와 요약이 다른 글자로 말하면 사람이 같은 사건인 줄
    모른다 — `_dur` 은 `gitops._fmt_seconds` 의 거울이다."""
    text = render("git_failed", {"op": "git fetch", "kind": "timeout", "seconds": seconds})
    assert text == f"git fetch timed out after {_fmt_seconds(seconds)}"


# ── 웹 카탈로그와의 진짜 잠금 (F2b) ─────────────────────────────────────────

#: 줄 맨 앞의 **프로퍼티 정의**만 — `//` 로 시작하는 주석은 안 걸린다.
_PROP_RE = re.compile(r'^ {4}"outcome\.([a-z_]+)"\s*:', re.M)


def _catalogue(locale: str) -> list[str]:
    """`web/i18n.js` 의 한 로케일이 **실제로 정의한** outcome 코드. 문자열이 파일 어딘가에
    나타나는지 보는 검사는 잠금이 아니다: KO 프로퍼티를 지우고 KO 영역 주석에 같은 문자열만
    남겨도 통과한다(검토 8 — 「넣지 않은 독」). 그래서 정의만 센다."""
    src = (Path(__file__).parents[1] / "src/remote_ci_monitor/web/i18n.js").read_text("utf-8")
    en, mark, ko = src.partition("  var KO = {")
    assert mark and "  var EN = {" in en, "i18n.js 의 구조가 바뀌었다 — 이 시험을 고쳐야 한다"
    return _PROP_RE.findall(en if locale == "en" else ko)


@pytest.mark.parametrize("locale", ["en", "ko"])
def test_every_outcome_code_is_defined_once_in_each_locale_of_the_web_catalogue(locale):
    """손으로 적은 목록은 생산자를 안 본다 — `tool_missing` 이 두 로케일 **모두**에 없는 채로
    M5j 부터 살아 있었고, `tests/web/i18n.test.js` 의 19개짜리 목록은 그걸 못 잡았다. 여기는
    `CODES` 를 직접 읽고 두 로케일의 **정의**를 각각 센다."""
    found = _catalogue(locale)
    assert len(found) == len(set(found)), f"{locale}: 같은 코드를 두 번 정의했다"
    assert set(found) == set(CODES), (
        f"{locale} 카탈로그와 CODES 가 다르다: "
        f"없는 것 {sorted(set(CODES) - set(found))}, 남는 것 {sorted(set(found) - set(CODES))}"
    )


def test_the_catalogue_lock_would_notice_a_code_that_only_appears_in_a_comment():
    """잠금 자체의 잠금. 주석에 적힌 문자열이 정의로 세어지면 위 시험은 아무것도 안 지킨다."""
    assert _PROP_RE.findall('    "outcome.start_failed": "x",') == ["start_failed"]
    assert _PROP_RE.findall('    // "outcome.start_failed": 옛 코드') == []
    assert _PROP_RE.findall('  var X = { "outcome.start_failed": 1 }') == []


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
