"""예시 파일 잠금 — launchd plist · systemd unit · server.toml · docs/ 의 M3 절 (명세 §4 · §5).

파일이 아직 없으면 빨갛다(구현이 만든다). launchctl/systemctl 은 부르지 않고 파싱만 한다.
"""

from __future__ import annotations

import configparser
import plistlib
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from remote_ci_monitor.config import load_server_config
from remote_ci_monitor.core.model import Progress
from remote_ci_monitor.core.progress import markers_from_log, progress_from_markers

ROOT = Path(__file__).resolve().parents[1]
PLIST = ROOT / "examples" / "launchd" / "com.remote-ci-monitor.server.plist"
UNIT = ROOT / "examples" / "systemd" / "rcm-server.service"
SERVER_TOML = ROOT / "examples" / "server.toml"
README = ROOT / "README.md"
OPERATING = ROOT / "docs" / "operating.md"
CONFIGURATION = ROOT / "docs" / "configuration.md"

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")

#: 두 서비스 파일이 서버에 주는 파일 서술자 한도. launchd 세션 기본값 256 으로는 죽는다.
NOFILE = 4096


def read_unit() -> configparser.ConfigParser:
    assert UNIT.is_file(), f"missing {UNIT.relative_to(ROOT)}"
    # systemd unit 은 INI 꼴이지만 `Environment=` 가 반복될 수 있어 strict=False
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    cp.read_string(UNIT.read_text())
    return cp


@pytest.fixture
def plist() -> dict[str, Any]:
    assert PLIST.is_file(), f"missing {PLIST.relative_to(ROOT)}"
    return plistlib.loads(PLIST.read_bytes())


@pytest.fixture
def unit() -> configparser.ConfigParser:
    return read_unit()


# ── launchd ──────────────────────────────────────────────────────────────────


def test_launchd_label_and_program(plist):
    assert plist["Label"] == "com.remote-ci-monitor.server"
    args = plist["ProgramArguments"]
    assert isinstance(args, list) and len(args) >= 2
    assert args[0].endswith("rcm") and args[0].startswith("/")  # launchd 는 절대 경로
    assert args[1] == "serve"
    assert "--config" in args
    assert args[args.index("--config") + 1].endswith("server.toml")


def test_launchd_lifecycle_keys(plist):
    assert plist["RunAtLoad"] is True
    keep = plist["KeepAlive"]
    # 명세 §4: 정상 종료(exit 0)면 되살리지 않는다 — dict 꼴이면 SuccessfulExit false
    if isinstance(keep, dict):
        assert keep.get("SuccessfulExit") is False
    else:
        assert keep is True
    assert isinstance(plist["ThrottleInterval"], int) and plist["ThrottleInterval"] >= 1


def test_launchd_logs_and_path(plist):
    assert plist["StandardErrorPath"].endswith(".log")
    assert plist["StandardOutPath"].endswith(".log")
    path = plist["EnvironmentVariables"]["PATH"]
    assert "/opt/homebrew/bin" in path.split(":")  # 프리셋이 부르는 도구가 여기 있다
    assert "/usr/bin" in path.split(":")


def test_launchd_gives_the_server_file_descriptor_headroom(plist):
    """launchd 세션의 기본 `maxfiles` 는 256 이다 — 서버가 그 안에서 죽는다.

    2026-09-08 07:48 운영: fd 가 마르자 `sqlite3.connect` 부터 실패해 모든 요청이
    `OperationalError` 가 됐고(claim 214 · heartbeat 93 · status 7) 알림 훅은
    `cannot start '/bin/bash': Too many open files` 로 죽었다. 재시작 전까지 12분간 안 풀렸다.
    systemd 쪽에는 `LimitNOFILE=4096` 이 처음부터 있었고 macOS 예시에만 없었다.
    """
    limits = plist["SoftResourceLimits"]
    assert limits["NumberOfFiles"] >= NOFILE, "systemd 의 LimitNOFILE 과 같은 숫자여야 한다"


def test_systemd_gives_the_server_the_same_file_descriptor_headroom(unit):
    """두 서비스 파일이 **같은 숫자**를 말한다 — 문서가 한 숫자를 말할 수 있게."""
    assert int(unit["Service"]["LimitNOFILE"]) >= NOFILE


def test_both_service_files_agree_on_the_number():
    plist_n = plistlib.loads(PLIST.read_bytes())["SoftResourceLimits"]["NumberOfFiles"]
    unit_n = int(read_unit()["Service"]["LimitNOFILE"])
    assert plist_n == unit_n == NOFILE


def test_launchd_comments_say_why_the_limit_is_there():
    text = PLIST.read_text()
    assert "maxfiles" in text or "file descriptor" in text
    assert "256" in text  # 기본값이 얼마라서 올리는지


def test_launchd_comments_guide_the_operator():
    text = PLIST.read_text()
    assert "launchctl bootstrap" in text
    assert "caffeinate" in text or "pmset" in text  # 잠자기 금지


# ── systemd ──────────────────────────────────────────────────────────────────


def test_systemd_unit_ordering_and_install(unit):
    assert "network-online.target" in unit["Unit"]["After"]
    assert unit["Install"]["WantedBy"].strip()


def test_systemd_service_exec_and_restart(unit):
    svc = unit["Service"]
    exec_start = svc["ExecStart"]
    assert exec_start.startswith("/")  # systemd 는 절대 경로만 받는다
    assert "rcm serve" in exec_start and "--config" in exec_start
    assert svc["User"].strip()
    assert svc["Restart"] in ("on-failure", "always")
    assert svc["RestartSec"].strip()
    assert svc["KillSignal"] == "SIGTERM"  # rcm serve 의 SIGTERM = 정상 종료(실행 중 잡 lost)
    assert svc["TimeoutStopSec"].strip()


def test_systemd_service_env_and_hardening(unit):
    text = UNIT.read_text()
    assert "PYTHONUNBUFFERED=1" in text
    assert unit["Service"]["NoNewPrivileges"].lower() == "true"


# ── examples/server.toml ─────────────────────────────────────────────────────


@needs_git
def test_example_server_config_loads():
    cfg = load_server_config(SERVER_TOML, environ={})
    assert cfg.preset("gate") is not None
    deploy = cfg.preset("deploy")
    # 예시가 주석 처리돼 있으면 None. 살아 있으면 git_ref 프리셋이고 repo 가 채워져 있어야 한다
    if deploy is not None:
        assert "git_ref" in deploy.source_modes and deploy.repo


def test_example_server_config_shows_git_ref():
    text = SERVER_TOML.read_text()
    assert "[[repos]]" in text
    assert re.search(r'^\s*#?\s*name\s*=\s*"deploy"', text, re.M), "no deploy preset example"
    assert re.search(r'^\s*#?\s*source_modes\s*=\s*\["git_ref"\]', text, re.M)
    assert "git_fetch_timeout_seconds" in text
    assert "retention_sweep_interval_seconds" in text


# ── README ───────────────────────────────────────────────────────────────────


def _section(text: str, heading: str) -> str:
    m = re.search(rf"^#{{2,3}}\s+{re.escape(heading)}\s*$", text, re.M)
    assert m, f"no '{heading}' heading"
    rest = text[m.end() :]
    nxt = re.search(r"^##\s", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


def test_docs_run_as_a_service():
    sec = _section(OPERATING.read_text(), "Run as a service")
    assert "launchctl" in sec and "systemctl" in sec
    assert "SIGTERM" in sec and "lost" in sec  # 서버 SIGTERM = 실행 중 잡 lost
    assert "caffeinate" in sec or "pmset" in sec  # 잠자기 금지


def test_docs_say_why_the_service_needs_file_descriptor_headroom():
    """운영이 fd 고갈로 12분 죽은 뒤 넣은 값이다 — 왜 그 숫자인지가 문서에 남아야 한다."""
    sec = _section(OPERATING.read_text(), "Run as a service")
    assert str(NOFILE) in sec
    assert "maxfiles" in sec and "256" in sec  # launchd 기본값이 모자란다는 근거
    assert "LimitNOFILE" in sec and "NumberOfFiles" in sec  # 두 서비스 파일의 키 이름


def test_docs_say_a_failed_step_has_to_be_declared():
    """선언하지 않으면 실패 스텝이 없다 — 이 문단이 사라지면 빨개진다.

    dev 의 PR #71 은 같은 자리에서 「추측이라고 밝힌다」를 잠갔다. M5h(결정 63)가 그 답을
    **이름을 안 댄다**로 바꿨으므로 잠그는 문장도 바뀐다 — 약화가 아니라 같은 사실의 다음
    판이다: 왜 틀리는지(병렬·되재생)와 무엇이 그 자리를 대신하는지(`last_step`)를 함께 건다.
    """
    text = CONFIGURATION.read_text()
    assert "::rcm::step-end::fail" in text
    assert "::rcm::fail::" in text  # 이름으로 지목하는 새 마커
    assert "last_step" in text  # 선언이 없을 때 그 자리를 대신하는 칸
    assert "declared" in text.lower()
    # 왜 틀리는지(병렬로 돌리고 마커를 몰아 찍는 스크립트)가 같이 있어야 한다
    assert "parallel" in text.lower()


def test_docs_document_basic_read_auth():
    text = OPERATING.read_text()
    assert 'read_auth = "basic"' in text
    assert "username" in text and "token name" in text  # 사용자명 = 토큰 이름, 비밀번호 = 토큰
    assert "TLS" in text  # Basic 은 평문 — TLS 프록시 뒤에서만


def test_docs_document_git_ref_runs():
    text = CONFIGURATION.read_text()
    assert "--ref" in text
    assert "[[repos]]" in text
    assert "git submodule" in text  # 서브모듈은 프리셋 스크립트가 직접 update --init


# ── examples/preset/name-failures.sh — 검증된 래퍼 (M5i I1 · 결정 77) ───────────
#
# 서버는 임의 출력을 파싱하지 않는다(`fail_patterns` 는 없다). 스크립트를 못 고칠 때 쓰는 이
# 래퍼가 그 팀이 소유한 **정확한** 형식의 줄을 마커로 옮긴다. 래퍼가 내는 줄을 서버의 진짜
# 소비자(`markers_from_log` → `progress_from_markers`)에 넣어 본다 — 문자열만 맞춘 예시는
# 서버가 다르게 읽어도 초록이다.

WRAPPER = ROOT / "examples" / "preset" / "name-failures.sh"

needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not on PATH")


def wrap(*args: str) -> subprocess.CompletedProcess[str]:
    assert WRAPPER.is_file(), f"missing {WRAPPER.relative_to(ROOT)}"
    return subprocess.run(["bash", str(WRAPPER), *args], capture_output=True, text=True)


def consumed(stdout: str, *, exit_code: int) -> Progress:
    """래퍼의 stdout 을 서버가 읽듯 읽는다."""
    now = datetime(2026, 9, 10, 9, 0, tzinfo=UTC)
    markers = markers_from_log([(now, ln) for ln in stdout.splitlines()])
    return progress_from_markers(
        markers, started_at=now, finished_at=now, now=now, exit_code=exit_code
    )


@needs_bash
def test_wrapper_passes_the_output_through_then_names_the_failures_then_ends_the_step():
    """순서: 스텝 → 원 출력 그대로 → 이름 → 스텝 끝. 종료 코드는 감싼 명령의 것이다."""
    script = (
        'echo building; echo "FAIL: test/a_test.dart"; echo "FAIL: test/a_test.dart";'
        ' echo "note: FAIL: elsewhere is only a mention";'
        ' echo "FAIL: test/b_test.dart" >&2; exit 7'
    )
    p = wrap("--step", "test", "--", "bash", "-c", script)
    assert p.returncode == 7, p.stderr
    assert p.stdout.splitlines() == [
        "::rcm::step::test",
        "building",
        "FAIL: test/a_test.dart",
        "FAIL: test/a_test.dart",
        "note: FAIL: elsewhere is only a mention",
        "FAIL: test/b_test.dart",
        "::rcm::fail::test/a_test.dart",  # 한 번만 — 서로 다른 이름만
        "::rcm::fail::test/b_test.dart",
        "::rcm::step-end::fail",
    ]
    progress = consumed(p.stdout, exit_code=7)
    assert progress.failed_step == "test"
    # `step-end::fail` 도 선언이라 서버는 그 스텝을 대장 **뒤**에 붙인다(결정 65 · 검증 라운드 5)
    assert progress.fail_names == ("test/a_test.dart", "test/b_test.dart", "test")


@needs_bash
def test_wrapper_names_nothing_for_a_green_command():
    """성공한 명령의 `FAIL:` 언급은 판정이 아니다 — 이름을 안 내고 스텝은 ok 로 닫는다."""
    p = wrap("--step", "test", "--", "bash", "-c", 'echo "FAIL: was fixed"; echo all green')
    assert p.returncode == 0, p.stderr
    assert p.stdout.splitlines() == [
        "::rcm::step::test",
        "FAIL: was fixed",
        "all green",
        "::rcm::step-end::ok",
    ]
    assert consumed(p.stdout, exit_code=0).fail_names == ()


@needs_bash
def test_wrapper_does_not_guess_when_the_command_fails_without_a_matching_line():
    """맞는 줄이 없으면 단위 이름은 없다. 스텝은 그 명령의 종료 코드로 닫히고 — 래퍼는 그 코드를
    **안다** — 서버는 그 선언만 대장에 남긴다. 추측한 단위 이름은 없다."""
    p = wrap("--step", "test", "--", "bash", "-c", "echo boom; exit 3")
    assert p.returncode == 3
    assert p.stdout.splitlines() == ["::rcm::step::test", "boom", "::rcm::step-end::fail"]
    progress = consumed(p.stdout, exit_code=3)
    assert progress.failed_step == "test" and progress.fail_names == ("test",)


@needs_bash
def test_wrapper_without_a_step_only_names():
    p = wrap("--", "bash", "-c", 'echo "FAIL: x"; exit 1')
    assert p.returncode == 1
    assert p.stdout.splitlines() == ["FAIL: x", "::rcm::fail::x"]


@needs_bash
def test_wrapper_strips_control_characters_and_caps_a_name_at_120():
    """서버의 `clean_name` 과 같은 규칙 — 마커가 터미널에 색·커서 이동을 실어 보내지 않는다."""
    script = (
        r'printf "FAIL: \033[31mred\033[0m\r\n";'
        r' printf "FAIL: %s\n" "$(printf "x%.0s" $(seq 200))"; exit 1'
    )
    p = wrap("--", "bash", "-c", script)
    assert p.returncode == 1
    markers = [ln for ln in p.stdout.splitlines() if ln.startswith("::rcm::fail::")]
    assert len(markers) == 2, p.stdout
    assert "\x1b" not in markers[0] and "\r" not in markers[0]
    assert markers[1] == "::rcm::fail::" + "x" * 120


@needs_bash
def test_wrapper_takes_the_exact_format_the_script_prints():
    """느슨한 `FAIL` 전체 검색이 아니라 그 팀의 형식(TAP 이면 TAP) — 언급이 판정이 되지 않게."""
    script = 'echo "ok 1 - a"; echo "not ok 2 - b"; echo "# FAIL: mentioned in a comment"; exit 1'
    p = wrap("--pattern", "^not ok [0-9]+ - (.+)$", "--", "bash", "-c", script)
    assert p.returncode == 1
    markers = [ln for ln in p.stdout.splitlines() if ln.startswith("::rcm::")]
    assert markers == ["::rcm::fail::b"]


@needs_bash
def test_wrapper_keeps_an_exit_code_the_shell_gave():
    """명령이 없으면 127 — 파이프 뒤의 `tee` 가 0 을 돌려줘도 래퍼는 `PIPESTATUS[0]` 을 준다."""
    p = wrap("--", "definitely-not-a-command-8f3a")
    assert p.returncode == 127


@needs_bash
def test_wrapper_refuses_a_call_without_a_command():
    p = wrap("--step", "test")
    assert p.returncode == 2 and "usage" in p.stderr


def test_wrapper_says_what_it_relies_on():
    assert WRAPPER.is_file(), f"missing {WRAPPER.relative_to(ROOT)}"
    text = WRAPPER.read_text()
    assert text.startswith("#!/usr/bin/env bash")
    assert "PIPESTATUS" in text
    assert "buffer" in text  # 파이프 뒤의 대상이 block-buffering 될 수 있다
    assert "guess" in text  # 맞는 줄이 없으면 이름도 없다


def test_docs_show_both_ways_to_name_a_failure():
    """결정 77 — 판정 함수에서 마커를 찍는 예시가 기본, 래퍼는 스크립트를 못 고칠 때. 마커가 없는
    실패는 **의도대로** 대장이 비고, 서버는 출력을 파싱하지 않는다."""
    sec = _section(CONFIGURATION.read_text(), "Making a failure explain itself")
    assert "::rcm::fail::" in sec
    assert "examples/preset/name-failures.sh" in sec
    assert "by design" in sec
    assert "never parses" in sec
