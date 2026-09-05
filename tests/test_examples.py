"""예시 파일 잠금 — launchd plist · systemd unit · server.toml · README 의 M3 절 (명세 §4 · §5).

파일이 아직 없으면 빨갛다(구현이 만든다). launchctl/systemctl 은 부르지 않고 파싱만 한다.
"""

from __future__ import annotations

import configparser
import plistlib
import re
import shutil
from pathlib import Path
from typing import Any

import pytest

from remote_ci_monitor.config import load_server_config

ROOT = Path(__file__).resolve().parents[1]
PLIST = ROOT / "examples" / "launchd" / "com.remote-ci-monitor.server.plist"
UNIT = ROOT / "examples" / "systemd" / "rcm-server.service"
SERVER_TOML = ROOT / "examples" / "server.toml"
README = ROOT / "README.md"

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")


@pytest.fixture
def plist() -> dict[str, Any]:
    assert PLIST.is_file(), f"missing {PLIST.relative_to(ROOT)}"
    return plistlib.loads(PLIST.read_bytes())


@pytest.fixture
def unit() -> configparser.ConfigParser:
    assert UNIT.is_file(), f"missing {UNIT.relative_to(ROOT)}"
    # systemd unit 은 INI 꼴이지만 `Environment=` 가 반복될 수 있어 strict=False
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    cp.read_string(UNIT.read_text())
    return cp


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
    assert m, f"README has no '{heading}' heading"
    rest = text[m.end() :]
    nxt = re.search(r"^##\s", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


def test_readme_run_as_a_service():
    sec = _section(README.read_text(), "Run as a service")
    assert "launchctl" in sec and "systemctl" in sec
    assert "SIGTERM" in sec and "lost" in sec  # 서버 SIGTERM = 실행 중 잡 lost
    assert "caffeinate" in sec or "pmset" in sec  # 잠자기 금지


def test_readme_documents_basic_read_auth():
    text = README.read_text()
    assert 'read_auth = "basic"' in text
    assert "username" in text and "token name" in text  # 사용자명 = 토큰 이름, 비밀번호 = 토큰
    assert "TLS" in text  # Basic 은 평문 — TLS 프록시 뒤에서만


def test_readme_documents_git_ref_runs():
    text = README.read_text()
    assert "--ref" in text
    assert "[[repos]]" in text
    assert "git submodule" in text  # 서브모듈은 프리셋 스크립트가 직접 update --init
