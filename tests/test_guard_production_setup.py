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


# ── P3 1라운드: 셸이 펼치는 값 · 반복한 옵션 · 운영 서비스 파일 자리 ──────────


@pytest.mark.parametrize(
    "flag",
    [
        "--data-dir $HOME/.local/share/rcm",
        "--data-dir ${HOME}/.local/share/rcm",
        "--data-dir=$HOME/.local/share/rcm/inner",
        '--data-dir "$HOME/.local/share/rcm"',
    ],
)
def test_tr33_a_data_dir_written_with_a_shell_variable_is_denied(flag):
    # P3 v2-1: 셸이 `$HOME` 을 펼친 뒤 setup 이 받는다 — 훅도 펼쳐서 본다
    verdict = judge(f"{SERVER} {flag} --label test.rcm.server", environ=TEST_XDG)
    assert verdict is not None and verdict.decision == "deny", flag
    assert "data" in verdict.reason


def test_tr33_pwd_is_the_working_folder_of_that_segment():
    # P3 v2-1: `$PWD` 는 그 조각의 작업 폴더다(`cd` 뒤에는 그 폴더)
    command = f"{SERVER} --data-dir $PWD/rcm --label test.rcm.server"
    assert denied(command, environ=TEST_XDG, cwd=HOME / ".local" / "share")
    assert denied(f"cd /home/op/.local/share && {command}", environ=TEST_XDG)
    assert judge(command, environ=TEST_XDG, cwd=Path("/home/op/rcm-devtest")) is None


def test_tr20_a_config_written_with_a_shell_variable_is_denied():
    # P3 v2-1: `--config $HOME/...` 과 `XDG_CONFIG_HOME=$HOME/.config` 은 운영 설정 폴더다
    assert denied(
        f"{WORKER} --label test.rcm.worker --config $HOME/.config/rcm/worker.toml",
        environ=TEST_XDG,
    )
    assert denied(
        f"{WORKER} --label test.rcm.worker --config ${{HOME}}/.config/rcm/worker.toml",
        environ=TEST_XDG,
    )
    assert denied(f"XDG_CONFIG_HOME=$HOME/.config {WORKER} --label test.rcm.worker")
    assert denied(f"XDG_CONFIG_HOME=${{HOME}}/.config {WORKER} --label test.rcm.worker")
    # 시험 자리를 가리키는 변수는 막지 않는다
    assert (
        judge(f"XDG_CONFIG_HOME=$HOME/rcm-devtest/config {WORKER} --label test.rcm.worker") is None
    )


def test_tr33_the_shell_expands_tilde_and_variables_with_its_own_home_not_the_prefix():
    # P3 v2-1: `HOME=… rcm setup --data-dir ~/…` 의 `~` 와 `$HOME` 은 셸이 제 HOME 으로 펼친다 —
    # 앞의 `HOME=` 은 명령에만 간다
    prefix = "HOME=/home/op/rcm-devtest/home"
    for value in ("~/.local/share/rcm", "$HOME/.local/share/rcm"):
        command = f"{prefix} {SERVER} --data-dir {value} --label test.rcm.server"
        assert denied(command, environ=TEST_XDG), value


def test_tr20_a_label_written_with_a_shell_variable_is_denied():
    # P3 v2-1: 라벨도 셸이 펼친 값이 setup 에 간다
    environ = {**TEST_XDG, "PROD_LABEL": "com.remote-ci-monitor.server"}
    command = f"{SERVER} --data-dir /tmp/d --label $PROD_LABEL"
    verdict = judge(command, environ=environ)
    assert verdict is not None and verdict.decision == "deny"
    assert "label" in verdict.reason


def test_tr33_a_repeated_option_is_read_as_argparse_reads_it():
    # P3 v2-1 · v1-1: argparse 는 같은 옵션을 두 번 주면 마지막 것을 쓴다 — 훅은 모두 본다
    command = (
        f"{SERVER} --data-dir /tmp/d --data-dir /home/op/.local/share/rcm --label test.rcm.server"
    )
    assert denied(command, environ=TEST_XDG)
    command = f"{SERVER} --data-dir /tmp/d --label test.rcm.server --label rcm-server"
    assert denied(command, environ=TEST_XDG)


@pytest.mark.parametrize(
    "target",
    [
        "/home/op/Library/LaunchAgents/com.remote-ci-monitor.server.plist",
        "~/Library/LaunchAgents/com.remote-ci-monitor.worker.plist",
        "$HOME/.config/systemd/user/rcm-worker.service",
        "/home/op/.config/systemd/user/rcm-server.service.d/override.conf",
        "/etc/systemd/system/rcm-server.service",
        "/Library/LaunchDaemons/com.remote-ci-monitor.server.plist",
    ],
)
def test_tr20_a_service_file_on_a_production_service_place_is_denied(target):
    # P3 v1-2: `--service-file` 이 운영 서비스 파일 자리면 라벨이 달라도 운영 서비스를 덮는다
    command = f"{SERVER} --data-dir /tmp/d --label test.rcm.server --service-file {target}"
    verdict = judge(f"{command} --force service", environ=TEST_XDG)
    assert verdict is not None and verdict.decision == "deny", target
    assert "service" in verdict.reason


def test_tr20_a_service_file_of_its_own_is_free():
    command = (
        f"{SERVER} --data-dir /tmp/d --label test.rcm.server "
        "--service-file /home/op/rcm-devtest/service/test.rcm.server.plist"
    )
    assert judge(command, environ=TEST_XDG) is None


def test_tr20_every_production_service_unit_is_a_place_setup_may_not_write():
    # P3 v1-2: 훅이 운영 venv 를 찾는 서비스 유닛(SERVICE_UNITS)은 모두 그 자리 안이다
    for unit in guard.SERVICE_UNITS:
        command = f"{SERVER} --data-dir /tmp/d --label test.rcm.server --service-file {unit}"
        assert denied(command, environ=TEST_XDG), unit


def test_tr20_guard_constants_match_the_setup_module():
    # P3 v2-9: 훅은 패키지를 들여오지 않아 사본을 든다 — 사본이 core 와 같아야 한다
    assert set(guard.SETUP_STANDARD_LABELS) == set(core.STANDARD_LABELS.values())
    assert guard._STANDARD_UNITS == core._STANDARD_UNITS
    for role, label in guard.SETUP_ROLE_LABELS.items():
        assert core.default_label(role) == label, role
    assert core.default_label("client") is None and "client" not in guard.SETUP_ROLE_LABELS
    assert set(guard.SETUP_SERVER_ROLES) == set(core.SERVER_ROLES)
    # 서비스 관리자가 읽는 자리 — guard 가 막는 자리와 setup 이 거절하는 자리가 같다
    places = {str(p).replace(str(HOME), "~", 1) for p in core.install_places(HOME)}
    assert set(guard.SETUP_SERVICE_PLACES) == places


# ── P3 2라운드: guard 는 셸 단계만 하고 setup 의 해석기(`core.where`)를 그대로 쓴다 ──


def test_tr20_an_empty_value_is_denied():
    # P3 2라운드 v2-1: 빈 `--config` 는 옛 setup 에서 기본 자리(운영 설정 폴더)로 떨어졌다. 이제
    # setup 이 종료 2 로 거절하고, 해석기가 거절하는 명령은 guard 도 막는다(fail closed)
    worker = f"{WORKER} --label test.rcm.worker"
    for flag in ("--config=", "--config ''", '--config "$UNSET_RCM_GUARD_TEST"', "--config ' '"):
        verdict = judge(f"{worker} {flag}")
        assert verdict is not None and verdict.decision == "deny", flag
    command = f"{SERVER} --label test.rcm.server --data-dir ''"
    assert denied(command, environ=TEST_XDG, cwd=HOME / ".local" / "share" / "rcm")


@pytest.mark.parametrize(
    "value",
    [
        "${XDG_DATA_HOME:-$HOME/.local/share}/rcm",
        "${HOME%/}/.local/share/rcm",
        '"$(echo /home/op/.local/share/rcm)"',
        "`echo /home/op/.local/share/rcm`",
        "$1/.local/share/rcm",
        "$UNSET_RCM_GUARD_TEST/rcm",
    ],
)
def test_tr33_a_value_the_hook_cannot_expand_is_denied(value):
    # P3 2라운드 v2-1: 셸 단계를 다 한 뒤에도 `$` · 명령 치환이 남으면 setup 이 받을 값을 모른다
    # — 운영으로 본다
    verdict = judge(f"{SERVER} --label test.rcm.server --data-dir {value}", environ=TEST_XDG)
    assert verdict is not None and verdict.decision == "deny", value


def test_tr20_an_unknown_role_or_label_is_denied():
    # P3 2라운드: 역할 · 라벨을 모르면 운영으로 본다
    for flags in (
        '--role "$UNSET_RCM_GUARD_TEST" --label test.rcm.server',
        "--role bogus --label test.rcm.server",
        "--role coordinator --label $'x\\n'",
    ):
        command = f"rcm setup {flags} --bind 127.0.0.1 --data-dir /tmp/d --yes"
        verdict = judge(command, environ=TEST_XDG)
        assert verdict is not None and verdict.decision == "deny", flags


def test_tr20_a_production_path_in_another_case_is_denied_on_a_case_insensitive_disk(
    monkeypatch,
):
    # P3 2라운드: macOS 의 기본 파일시스템은 대소문자를 가리지 않는다 — `~/.config/RCM` 은 운영
    # 설정 폴더이고 `~/library/launchagents` 는 서비스 관리자가 읽는 자리다
    monkeypatch.setattr(guard, "FOLD_CASE", True, raising=False)
    monkeypatch.setattr(core, "FOLD_CASE", True, raising=False)
    worker = f"{WORKER} --label test.rcm.worker"
    assert denied(f"{worker} --config ~/.config/RCM/worker.toml", environ=TEST_XDG)
    server = f"{SERVER} --label test.rcm.server"
    assert denied(f"{server} --data-dir ~/.Local/Share/RCM", environ=TEST_XDG)
    target = "~/library/launchagents/test.rcm.server.plist"
    assert denied(f"{server} --data-dir /tmp/d --service-file {target}", environ=TEST_XDG)
    # 대소문자를 가리는 디스크(Linux)에서는 다른 폴더다
    monkeypatch.setattr(guard, "FOLD_CASE", False)
    monkeypatch.setattr(core, "FOLD_CASE", False)
    assert judge(f"{worker} --config ~/.config/RCM/worker.toml", environ=TEST_XDG) is None


@pytest.mark.parametrize(
    "head",
    [
        "/usr/bin/env rcm",
        "/usr/bin/env -i HOME=/home/op rcm",
        "/usr/bin/nohup rcm",
        "python -u -m remote_ci_monitor",
        "python3 -X dev -W ignore -m remote_ci_monitor.cli",
        "/srv/x/.venv/bin/python3.11 -I -mremote_ci_monitor",
        "python -m remote_ci_monitor --no-follow",
    ],
)
def test_tr20_a_wrapper_path_or_interpreter_flag_still_reaches_setup(head):
    # P3 2라운드: 감싸기와 python 은 이름(basename)으로 보고, python 의 플래그는 건너뛴다
    tail = "setup --role worker --server http://10.0.0.5:18790 --pool p --lanes 1 --yes"
    verdict = judge(f"{head} {tail} --label test.rcm.worker")
    assert verdict is not None and verdict.decision == "deny", head


def test_tr20_the_hook_refuses_setup_when_the_resolver_does_not_load(monkeypatch):
    # P3 2라운드: 해석기(`remote_ci_monitor.core.setup`)를 못 들여오면 setup 이 어디에 쓸지 모른다
    # — 거절하고 이유를 댄다(fail closed)
    free = f"{SERVER} --data-dir /tmp/d --label test.rcm.server"
    assert judge(free, environ=TEST_XDG) is None
    monkeypatch.setitem(sys.modules, "remote_ci_monitor.core.setup", None)
    verdict = judge(free, environ=TEST_XDG)
    assert verdict is not None and verdict.decision == "deny"
    assert "remote_ci_monitor.core.setup" in verdict.reason
    # setup 이 아닌 명령은 그대로다
    assert judge("ls /tmp", environ=TEST_XDG) is None


def test_tr20_a_client_writes_no_service_file_so_its_service_file_flag_is_free():
    # P3 2라운드 LOW: client 는 서비스 파일을 쓰지 않는다 — 해석기의 답에 없는 자리는 막지 않는다
    target = "~/Library/LaunchAgents/com.remote-ci-monitor.server.plist"
    client = "rcm setup --role client --server http://10.0.0.5:18790 --yes"
    assert judge(f"{client} --service-file {target}", environ=TEST_XDG) is None


def test_tr20_redirections_comments_and_quoting_read_as_the_shell_reads_them():
    # 셸 단계의 잠금: 리다이렉션 · 주석은 setup 의 인자가 아니고, 작은따옴표 안의 `$` 는 글자다
    free = f"{SERVER} --data-dir /tmp/d --label test.rcm.server"
    for tail in ("> /tmp/log 2>&1", "2> /tmp/err # a note", "</dev/null &>/tmp/x", "&"):
        assert judge(f"{free} {tail}", environ=TEST_XDG) is None, tail
    quoted = f"{SERVER} --data-dir '/tmp/$HOME' --label test.rcm.server"
    assert judge(quoted, environ=TEST_XDG) is None
    # 반복한 옵션: setup 은 마지막 값을 쓰지만 guard 는 앞의 값도 본다(막는 쪽)
    command = f"{SERVER} --data-dir /home/op/.local/share/rcm --data-dir /tmp/d --label t.s"
    assert denied(command, environ=TEST_XDG)
