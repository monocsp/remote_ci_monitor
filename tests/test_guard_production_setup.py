"""`tools/guard_production.py` 의 `rcm setup` 규칙 셋(M5t-a §4.1, TR20 · TR33, 6-A-L4 · 6-B-V5 ·
6-B-V1) — 판정 함수 `decide` 를 훅 입력 그대로 부른다(기존 tests/test_guard_production.py 방식).

운영 설치는 가짜 경로(`/home/op`)다. 막는 것: setup 이 운영 설정 폴더에 쓰는 것, 운영 라벨의 서비스
파일을 쓰는 것, 운영 `data_dir`(그 안 포함)을 고르는 것. 막지 않는 것: 시험 HOME · XDG · 라벨 ·
`data_dir` 로 하는 setup, 운영 설치가 없는 컴퓨터.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from remote_ci_monitor.core import setup as core

ROOT = Path(__file__).resolve().parents[1]


def _load_guard():
    path = ROOT / "tools" / "guard_production.py"
    spec = importlib.util.spec_from_file_location("guard_production_setup", path)
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


guard = _load_guard()

HOME = Path("/home/op")
PROD = guard.Production(
    checkout=Path("/srv/rcm/checkout"),
    venv=Path("/srv/rcm/venv"),
    config_dir=HOME / ".config" / "rcm",
    data_dir=HOME / ".local" / "share" / "rcm",
)
SESSION = {"HOME": str(HOME), "PATH": "/usr/bin:/bin"}
TEST_XDG = {**SESSION, "XDG_CONFIG_HOME": "/home/op/rcm-devtest/config"}
WORKTREE = Path("/home/op/src/remote_ci_monitor-x")
WORKER = "rcm setup --role worker --server http://10.0.0.5:18790 --pool p --lanes 1 --yes"
SERVER = "rcm setup --role coordinator-worker --bind 127.0.0.1 --port 18790 --yes"


def judge(command: str, environ=SESSION, cwd: Path = WORKTREE, prod=PROD):
    return guard.decide("Bash", {"command": command}, prod, cwd, environ=environ)


def denied(command: str, **kw) -> bool:
    verdict = judge(command, **kw)
    return verdict is not None and verdict.decision == "deny"


# ── 첫째 규칙: 운영 설정 경로 ────────────────────────────────────────────────


def test_tr20_setup_that_would_write_the_production_config_folder_is_denied():
    # XDG 가 없으면 setup 은 $HOME/.config/rcm 에 쓴다 — 운영 설정 폴더다
    verdict = judge(f"{WORKER} --label test.rcm.worker")
    assert verdict is not None and verdict.decision == "deny"
    assert "config" in verdict.reason
    assert denied(f"{WORKER} --label test.rcm.worker --config /home/op/.config/rcm/worker.toml")
    assert denied(
        f"{WORKER} --label test.rcm.worker --config /tmp/w.toml "
        "--service-file /home/op/.config/rcm/service/x.plist"
    )
    # XDG_CONFIG_HOME 이 운영 자리를 가리켜도 같다 — setup 과 같은 규칙으로 계산한다
    assert denied(
        f"XDG_CONFIG_HOME=/home/op/.config {WORKER} --label test.rcm.worker", environ=SESSION
    )


def test_tr20_setup_with_a_test_home_or_xdg_is_free():
    assert judge(f"{WORKER} --label test.rcm.worker", environ=TEST_XDG) is None
    assert judge(f"HOME=/home/op/rcm-devtest/home {WORKER} --label test.rcm.worker") is None
    assert judge(f"{WORKER} --label test.rcm.worker --config /tmp/w.toml") is None


# ── 둘째 규칙: 운영 라벨 ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "label",
    ["com.remote-ci-monitor.server", "com.remote-ci-monitor.worker", "rcm-server", "rcm-worker"],
)
def test_tr20_setup_that_would_write_a_production_label_is_denied(label):
    verdict = judge(f"{SERVER} --data-dir /tmp/d --label {label}", environ=TEST_XDG)
    assert verdict is not None and verdict.decision == "deny"
    assert "label" in verdict.reason


def test_tr20_no_label_means_the_standard_one_and_is_denied_but_a_client_has_none():
    assert denied(f"{SERVER} --data-dir /tmp/d", environ=TEST_XDG)
    assert denied(WORKER, environ=TEST_XDG)
    client = "rcm setup --role client --server http://10.0.0.5:18790 --yes"
    assert judge(client, environ=TEST_XDG) is None
    assert judge(f"{SERVER} --data-dir /tmp/d --label test.rcm.server", environ=TEST_XDG) is None


def test_tr20_guard_setup_and_doctor_share_one_systemd_unit_name_rule():
    # 6-B-V1: 표준 라벨은 rcm-server · rcm-worker, 다른 라벨은 <label>.service
    for label in (
        "com.remote-ci-monitor.server",
        "com.remote-ci-monitor.worker",
        "test.rcm.server",
        "rcm-server",
    ):
        assert guard.unit_name(label) == core.unit_name(label), label


# ── 셋째 규칙: 운영 data_dir ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "data",
    [
        "/home/op/.local/share/rcm",
        "/home/op/.local/share/rcm/",
        "/home/op/.local/share/rcm/inner",  # 「그 안」
        "~/.local/share/rcm",  # 그 명령의 HOME 으로 푼다
        "--data-dir=/home/op/.local/share/rcm",
    ],
)
def test_tr33_setup_on_the_production_data_dir_is_denied(data):
    flag = data if data.startswith("--") else f"--data-dir {data}"
    verdict = judge(f"{SERVER} {flag} --label test.rcm.server", environ=TEST_XDG)
    assert verdict is not None and verdict.decision == "deny"
    assert "data" in verdict.reason


def test_tr33_the_template_default_resolves_with_the_commands_home():
    # --data-dir 이 없으면 템플릿 기본값 ~/.local/share/rcm — 그 명령의 HOME 으로 푼다
    assert denied(f"{SERVER} --label test.rcm.server", environ=TEST_XDG)
    assert (
        judge(f"HOME=/home/op/rcm-devtest/home {SERVER} --label test.rcm.server", environ=TEST_XDG)
        is None
    )
    # 역할을 안 주면 setup 이 물을 수 있다 — 역할 2 · 4 로 본다
    assert denied("rcm setup --label test.rcm.server", environ=TEST_XDG)


def test_tr33_a_relative_data_dir_is_read_against_the_working_folder():
    command = f"{SERVER} --data-dir . --label test.rcm.server"
    assert denied(command, environ=TEST_XDG, cwd=HOME / ".local" / "share" / "rcm")
    assert judge(command, environ=TEST_XDG, cwd=Path("/home/op/rcm-devtest/data")) is None


def test_tr33_worker_and_client_roles_have_no_server_data_dir():
    worker = f"{WORKER} --label test.rcm.worker"
    assert judge(worker, environ=TEST_XDG) is None


def test_tr33_help_and_machines_without_a_production_install_are_free():
    assert judge("rcm setup --help") is None
    assert judge(f"{SERVER} --data-dir /home/op/.local/share/rcm", prod=guard.Production()) is None
