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


def test_tr33_a_relative_data_dir_is_denied_in_any_working_folder():
    # P3 4라운드: 훅은 cwd 를 믿지 않는다 — 시험 폴더에 서 있어도 상대 경로는 막는다(전에는
    # 시험 폴더에서 허용했다)
    command = f"{SERVER} --data-dir . --label test.rcm.server"
    assert denied(command, environ=TEST_XDG, cwd=HOME / ".local" / "share" / "rcm")
    assert denied(command, environ=TEST_XDG, cwd=Path("/home/op/rcm-devtest/data"))


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


def test_tr33_pwd_in_a_setup_command_is_denied():
    # P3 v2-1 에서는 `$PWD` 를 그 조각의 작업 폴더로 펼쳤다. P3 4라운드부터 `$` 가 든 setup 은
    # 허용 모양이 아니다 — 시험 폴더에서도 막는다
    command = f"{SERVER} --data-dir $PWD/rcm --label test.rcm.server"
    assert denied(command, environ=TEST_XDG, cwd=HOME / ".local" / "share")
    assert denied(f"cd /home/op/.local/share && {command}", environ=TEST_XDG)
    assert denied(command, environ=TEST_XDG, cwd=Path("/home/op/rcm-devtest"))


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
    # P3 4라운드: 시험 자리를 가리키는 변수도 막는다 — `$` 가 든 setup 은 허용 모양이 아니다.
    # 같은 자리를 글자로 쓰면 지나간다
    assert denied(f"XDG_CONFIG_HOME=$HOME/rcm-devtest/config {WORKER} --label test.rcm.worker")
    assert (
        judge(f"XDG_CONFIG_HOME=/home/op/rcm-devtest/config {WORKER} --label test.rcm.worker")
        is None
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
    worker = f"{WORKER} --label test.rcm.worker"
    assert denied(f"{worker} --config ~/.config/RCM/worker.toml", environ=TEST_XDG)
    server = f"{SERVER} --label test.rcm.server"
    assert denied(f"{server} --data-dir ~/.Local/Share/RCM", environ=TEST_XDG)
    target = "~/library/launchagents/test.rcm.server.plist"
    assert denied(f"{server} --data-dir /tmp/d --service-file {target}", environ=TEST_XDG)
    # 대소문자를 가리는 디스크(Linux)에서는 다른 폴더다
    monkeypatch.setattr(guard, "FOLD_CASE", False)
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


def test_tr20_redirections_comments_and_quoted_dollars_are_not_the_plain_shape():
    # P3 2라운드에서는 훅이 리다이렉션 · 주석 · 작은따옴표를 셸처럼 읽어 허용했다. P3 4라운드부터
    # 그런 글자가 든 setup 은 허용 모양이 아니다 — 막는다. 따옴표로 감싼 평범한 값은 그대로다
    free = f"{SERVER} --data-dir /tmp/d --label test.rcm.server"
    for tail in ("> /tmp/log 2>&1", "2> /tmp/err # a note", "</dev/null &>/tmp/x", "&"):
        assert denied(f"{free} {tail}", environ=TEST_XDG), tail
    quoted = f"{SERVER} --data-dir '/tmp/$HOME' --label test.rcm.server"
    assert denied(quoted, environ=TEST_XDG)
    assert judge(f"{SERVER} --data-dir '/tmp/d' --label test.rcm.server", environ=TEST_XDG) is None
    # 반복한 옵션: setup 은 마지막 값을 쓰지만 guard 는 앞의 값도 본다(막는 쪽)
    command = f"{SERVER} --data-dir /home/op/.local/share/rcm --data-dir /tmp/d --label t.s"
    assert denied(command, environ=TEST_XDG)


# ── P3 3라운드: setup 규칙의 「운영」은 집의 정의 하나다(`_edit_verdict` 와 같은 운영 뿌리 넷) ──

PROD_DATA = "/home/op/.local/share/rcm"
FREE_SERVICE = "--service-file /home/op/rcm-devtest/svc/test.rcm.worker.plist"


@pytest.mark.parametrize(
    "flags",
    [
        f"--config {PROD_DATA}/rcm.sqlite3 --force config",  # 운영 data_dir 의 DB 를 덮는다
        "--config /srv/rcm/checkout/x.toml",  # 운영 체크아웃
        "--config /srv/rcm/venv/x.toml",  # 서비스 venv
        # 서비스 관리자가 읽는 자리 — 설정 파일도 서비스 파일과 같은 규칙이다
        "--config ~/Library/LaunchAgents/com.remote-ci-monitor.server.plist",
    ],
)
def test_tr20_setup_may_not_write_its_config_into_any_production_root(flags):
    # P3 3라운드 v1-1(HIGH): setup 규칙이 운영 설정 폴더 하나만 「운영」으로 보았다. 편집 규칙
    # (`_edit_verdict`)이 막는 뿌리 넷과 서비스 관리자 자리를 설정 파일에도 댄다
    command = f"{WORKER} --label test.rcm.worker {FREE_SERVICE} {flags}"
    verdict = judge(command, environ=TEST_XDG)
    assert verdict is not None and verdict.decision == "deny", flags


def test_tr20_setup_may_not_write_its_service_file_into_any_production_root():
    # P3 3라운드 v1-1: 서비스 파일도 운영 뿌리 넷 안에는 쓰지 않는다
    for root in (PROD_DATA, "/srv/rcm/checkout", "/srv/rcm/venv"):
        command = (
            f"{SERVER} --data-dir /tmp/d --label test.rcm.server --service-file {root}/x.plist"
        )
        assert denied(command, environ=TEST_XDG), root


def test_tr20_the_setup_rule_and_the_edit_rule_share_one_production_list():
    # P3 3라운드 근본 원인: setup 규칙이 편집 규칙보다 좁은 「운영」 목록을 따로 들었다. 둘이 같은
    # 함수를 부르는지, 편집 규칙이 막는 자리마다 setup 도 막는지 본다
    for _name, root in PROD.labelled():
        target = root / "x.toml"
        assert guard.decide("Write", {"file_path": str(target)}, PROD, WORKTREE) is not None
        command = f"{WORKER} --label test.rcm.worker {FREE_SERVICE} --config {target}"
        assert denied(command, environ=TEST_XDG), root


# ── P3 3라운드: 닫는 쪽의 그물 — 풀어내지 못한 setup 은 막는다 ──

PROD_SETUP = f"setup --role coordinator --bind 127.0.0.1 --label t.s --data-dir {PROD_DATA} --yes"
#: 그물의 거절이 말하는 허용 모양(P3 4라운드)
PLAIN_MESSAGE = (
    "run `rcm setup` as one plain command: no ; & | $ ` ( ) < > or backslash, "
    "absolute or ~ paths only"
)


@pytest.mark.parametrize(
    "command",
    [
        f"env -C /tmp rcm {PROD_SETUP}",
        f"env -S 'rcm {PROD_SETUP}'",
        f"sudo -u op rcm {PROD_SETUP}",
        f"sudo -E rcm {PROD_SETUP}",
        f"command -p rcm {PROD_SETUP}",
        f"exec -a x rcm {PROD_SETUP}",
        f"time -p rcm {PROD_SETUP}",
        f"if rcm {PROD_SETUP}; then :; fi",
        f"for i in 1; do rcm {PROD_SETUP}; done",
        f"! rcm {PROD_SETUP}",
        f"true & rcm {PROD_SETUP}",
        f'echo \\" ; rcm {PROD_SETUP} ; echo \\"',
        f"python -Im remote_ci_monitor {PROD_SETUP}",
        f"R=rcm; $R {PROD_SETUP}",
        f"python -c 'from remote_ci_monitor.setup import main; main()' {PROD_SETUP[6:]}",
    ],
)
def test_tr20_a_setup_the_hook_cannot_read_as_a_plain_call_is_denied(command):
    # P3 3라운드 v1-2 · v1-3 · v2-3: 세그먼트 파서가 모르는 감싸기 플래그 · 셸 키워드 · 단일 `&` ·
    # 어긋난 따옴표 · 묶인 짧은 플래그 뒤의 setup 은 setup 규칙을 지났다. 명령 글에 rcm(또는 이
    # 패키지)과 낱말 setup 이 함께 있는데 평범한 setup 호출로 풀지 못하면 막는다
    verdict = judge(command, environ=TEST_XDG)
    assert verdict is not None and verdict.decision == "deny", command
    assert "plain `rcm setup" in verdict.reason, command


def test_tr20_the_net_lets_plain_setup_help_and_unrelated_commands_through():
    # 과잉 차단의 경계: 평범한 setup · 도움말 · setup 이 낱말로 나오지 않는 명령은 그대로다
    assert judge("rcm setup --help", environ=TEST_XDG) is None
    # P3 4라운드: 도움말도 평범한 명령 하나일 때만이다(`&&` 뒤에 무엇이든 붙일 수 있었다)
    assert denied("rcm setup -h && rcm setup --help", environ=TEST_XDG)
    assert judge(f"HOME=/home/op/rcm-devtest/home {WORKER} --label test.rcm.worker") is None
    for command in (
        "cd /home/op/src/remote_ci_monitor-setup-x && ./.venv/bin/python -m pytest -q",
        "./.venv/bin/ruff check src/remote_ci_monitor/setup.py tests/test_setup.py",
        "python -c 'import remote_ci_monitor' && git log -1 -- tests/test_setup.py",
        "rcm status && echo done",
    ):
        assert judge(command, environ=TEST_XDG) is None, command
    # 글에만 있어도 막힌다(받아들인 과잉 차단) — 메시지가 파일로 쓰는 길을 알려 준다
    verdict = judge('git commit -m "fix rcm setup"', environ=TEST_XDG)
    assert verdict is not None and verdict.decision == "deny"
    assert "-F" in verdict.reason
    # 운영 설치가 없는 컴퓨터에서는 그물도 없다
    assert judge(f"sudo -u op rcm {PROD_SETUP}", prod=guard.Production()) is None


@pytest.mark.parametrize(
    "head",
    [
        "python -u -m remote_ci_monitor",
        "python3 -X dev -W ignore -m remote_ci_monitor.cli",
        "/srv/x/.venv/bin/python3.11 -I -mremote_ci_monitor",
    ],
)
def test_tr20_python_flags_with_a_test_home_are_read_not_caught_by_the_net(head):
    # python 의 인터프리터 플래그는 허용 모양 안이다 — 시험 HOME 의 setup 은 막지 않는다. P3
    # 4라운드부터 감싸기(`/usr/bin/env rcm` · `/usr/bin/nohup rcm`)는 허용 모양 밖이라 막는다
    tail = "setup --role worker --server http://10.0.0.5:18790 --pool p --lanes 1 --yes"
    command = f"HOME=/home/op/rcm-devtest/home {head} {tail} --label test.rcm.worker"
    assert judge(command) is None, head
    for wrapper in ("/usr/bin/env rcm", "/usr/bin/nohup rcm"):
        assert denied(f"HOME=/home/op/rcm-devtest/home {wrapper} {tail} --label t.w"), wrapper


# ── P3 3라운드: 따라가지 못한 `cd` 뒤의 상대 경로 ──


@pytest.mark.parametrize(
    "cd",
    ["cd -P /home/op/.local/share", "cd -- /home/op/.local/share", "cd -", "cd $UNSET_RCM_D"],
)
def test_tr33_a_relative_path_after_a_cd_the_hook_cannot_follow_is_denied(cd):
    # P3 3라운드 v1-4: 훅이 cwd 를 모르면 setup 의 상대 경로가 어디인지 모른다 — 막는 쪽
    command = f"{cd} && {SERVER} --data-dir rcm --label test.rcm.server"
    verdict = judge(command, environ=TEST_XDG)
    assert verdict is not None and verdict.decision == "deny", cd
    assert PLAIN_MESSAGE in verdict.reason, cd


def test_tr33_any_cd_in_front_of_setup_is_denied():
    # P3 3라운드에서는 펼칠 수 있는 `cd` 를 따라가 그 뒤의 setup 을 허용했다. P3 4라운드부터
    # setup 은 평범한 명령 하나여야 한다 — 따라갈 수 있는 `cd` 도, 절대 경로뿐인 setup 도 막는다
    rel = f"{SERVER} --data-dir data --label test.rcm.server"
    assert denied(f"cd /home/op/rcm-devtest && {rel}", environ=TEST_XDG)
    assert denied(f"cd $HOME/rcm-devtest && {rel}", environ=TEST_XDG)
    assert denied(f"cd $HOME/.local/share && {SERVER} --data-dir rcm --label t.s", environ=TEST_XDG)
    absolute = f"{SERVER} --data-dir /tmp/d --label test.rcm.server"
    assert denied(f"cd - && {absolute}", environ=TEST_XDG)
    assert denied(f"cd -P /x && {absolute}", environ=TEST_XDG)
    assert denied(f"cd /home/op/rcm-devtest && {absolute}", environ=TEST_XDG)


# ── P3 3라운드: 훅이 터져도 setup 은 허용으로 새지 않는다 ──


def test_tr20_a_crash_while_judging_setup_is_a_deny(monkeypatch, capsys):
    # P3 3라운드 LOW: main() 의 catch-all 은 「훅이 터지면 막지 않는다」였다 — setup 판정 중의
    # 예외는 거절이다. setup 이 아닌 명령은 그대로 경고만 남긴다
    import io
    import json

    def boom(*_a, **_kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(guard, "find_production", lambda: PROD)
    monkeypatch.setattr(guard, "_plain_setup", boom)
    monkeypatch.setattr(guard, "_hit", boom)

    def hook(command: str) -> dict:
        payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": "/tmp"}
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
        assert guard.main() == 0
        return json.loads(capsys.readouterr().out or "{}")

    out = hook(f"HOME=/home/op/rcm-devtest/home {WORKER} --label test.rcm.worker")
    decision = out.get("hookSpecificOutput", {}).get("permissionDecision")
    assert decision == "deny", out
    assert "boom" in out["hookSpecificOutput"]["permissionDecisionReason"]
    out = hook("rm /tmp/scratch-file")
    assert "hookSpecificOutput" not in out and "boom" in out.get("systemMessage", ""), out


# ── P3 4라운드: setup 판정은 허용 목록이다 — 평범한 명령 하나만 해석기에 묻는다 ──

FREE_SERVER = f"{SERVER} --label test.rcm.server"


@pytest.mark.parametrize(
    ("command", "cwd"),
    [
        # 줄 잇기(`\`+줄바꿈)는 셸이 지운다 — `se\<NL>tup` 은 setup 이다
        (f"rcm se\\\ntup {PROD_SETUP[6:]}", WORKTREE),
        (f"{WORKER} --label t.w {FREE_SERVICE} --config /home/op/.con\\\nfig/rcm/x.toml", WORKTREE),
        # 도움말 뒤에 숨긴 setup
        (f"rcm setup --help & rcm {PROD_SETUP}", WORKTREE),
        (f"rcm setup -h $(rcm {PROD_SETUP})", WORKTREE),
        (f"rcm setup -h `rcm {PROD_SETUP}`", WORKTREE),
        # 실행되지 않거나 새지 않는 `cd` — 훅이 짐작한 cwd 는 셸의 cwd 가 아니다
        (
            f"cd /nonexistent; {SERVER} --label t.s --data-dir ../../home/op/.local/share/rcm",
            Path("/home/op/rcm-devtest/a"),
        ),
        (f"(cd /tmp); {SERVER} --label t.s --data-dir rcm", HOME / ".local" / "share"),
        (f"false && cd /tmp; {SERVER} --label t.s --data-dir rcm", HOME / ".local" / "share"),
        # 앞 세그먼트의 변수 · export
        (f"TMPDIR={PROD_DATA}; {SERVER} --label t.s --data-dir $TMPDIR", WORKTREE),
        (f"export XDG_CONFIG_HOME=/home/op/.config && {WORKER} --label t.w", WORKTREE),
        # 하위 명령 자리를 셸이 만든다
        (f"S=se; rcm ${{S}}tup {PROD_SETUP[6:]}", WORKTREE),
    ],
)
def test_tr20_round4_shell_tricks_around_setup_are_denied(command, cwd):
    # P3 4라운드 HIGH: 거부 목록으로는 셸 문법을 다 막지 못했다. 평범한 명령 하나가 아니면 막는다
    verdict = judge(command, environ=TEST_XDG, cwd=cwd)
    assert verdict is not None and verdict.decision == "deny", command


@pytest.mark.parametrize(
    "command",
    [
        f"rcm ${{S}}tup {PROD_SETUP[6:]}",
        f"rcm s{{e,}}tup {PROD_SETUP[6:]}",
        f"rcm${{IFS}}${{S}}tup {PROD_SETUP[6:]}",
        'rcm "$SUB" --role coordinator',
        "rcm --no-follow $SUB",
        "python -m remote_ci_monitor $SUB",
        f"R=rcm; caffeinate -i $R ${{S}}tup {PROD_SETUP[6:]}",
        "$R --no-follow ${S}tup",
        "rc? ${S}tup",
    ],
)
def test_tr20_a_subcommand_the_shell_makes_after_rcm_is_denied(command):
    # rcm(또는 명령 자리의 변수) 다음 하위 명령 자리가 글자 그대로가 아니면 setup 일 수 있다
    verdict = judge(command, environ=TEST_XDG)
    assert verdict is not None and verdict.decision == "deny", command
    assert PLAIN_MESSAGE in verdict.reason, command


def test_tr20_the_net_says_how_to_write_it():
    # 그물의 거절은 허용 모양을 그대로 말하고, 글에만 있는 경우의 길(파일)도 알려 준다
    for command in ('git commit -m "fix rcm setup"', f"sudo -u op rcm {PROD_SETUP}"):
        verdict = judge(command, environ=TEST_XDG)
        assert verdict is not None and verdict.decision == "deny", command
        assert PLAIN_MESSAGE in verdict.reason and "-F" in verdict.reason, command


@pytest.mark.parametrize(
    "command",
    [
        "rcm wait --job $N",
        "cd $W && rcm status",
        '"$PY" -m pytest -q tests/test_guard_production.py',
        "grep -n rcm docs/operating.md",
        "for f in $A; do echo $f; done",
        'if [[ -n $X ]]; then echo "$X"; fi',
    ],
)
def test_tr20_variables_around_rcm_without_setup_pass(command):
    # 과잉 차단의 경계: 하위 명령 자리가 글자 그대로이고 setup 이 없으면 그물은 조용하다
    assert judge(command, environ=TEST_XDG) is None, command


@pytest.mark.parametrize(
    "flags",
    [
        "--data-dir data",
        "--data-dir ./data",
        "--data-dir=../rcm",
        "--config w.toml",
        "--service-file svc/x.plist",
        "--data-dir ~op/data",  # `~user` 는 passwd 를 읽는다 — `~` · `~/…` 만
    ],
)
def test_tr33_a_relative_path_is_denied_wherever_the_session_stands(flags):
    # P3 4라운드: cwd 를 믿지 않는다 — 시험 폴더에 서 있어도 상대 경로는 막는다
    command = f"{FREE_SERVER} {flags}"
    verdict = judge(command, environ=TEST_XDG, cwd=Path("/home/op/rcm-devtest/data"))
    assert verdict is not None and verdict.decision == "deny", flags
    assert "absolute or ~ paths only" in verdict.reason, flags


@pytest.mark.parametrize("prefix", ["HOME=h", "XDG_CONFIG_HOME=cfg", "HOME=~op"])
def test_tr33_a_relative_home_or_config_home_in_front_is_denied(prefix):
    command = f"{prefix} {FREE_SERVER} --data-dir /tmp/d"
    verdict = judge(command, environ=TEST_XDG, cwd=Path("/home/op/rcm-devtest"))
    assert verdict is not None and verdict.decision == "deny", prefix


@pytest.mark.parametrize(
    "command",
    [
        "HOME=/tmp/h rcm setup --role worker --yes --server http://h:1 --pool default "
        "--lanes 1 --label test.rcm.worker",
        "rcm setup --help",
        "rcm setup -h",
        f"/abs/.venv/bin/rcm setup {FREE_SERVER[10:]} --data-dir /tmp/d",
        f"./.venv/bin/rcm setup {FREE_SERVER[10:]} --data-dir /tmp/d",
        f"python3 -m remote_ci_monitor setup {FREE_SERVER[10:]} --data-dir /tmp/d",
        f"python3.11 -u -m remote_ci_monitor.cli setup {FREE_SERVER[10:]} --data-dir /tmp/d",
        f"{FREE_SERVER} --data-dir '/tmp/a b/d' --config \"/tmp/a b/s.toml\"",
        f"{FREE_SERVER} --data-dir='/tmp/d' --config=/tmp/s.toml",
        f"{FREE_SERVER} --data-dir ~/rcm-devtest/data",
        f"XDG_CONFIG_HOME=~/rcm-devtest/cfg {FREE_SERVER} --data-dir /tmp/d",
    ],
)
def test_tr20_the_plain_shapes_reach_the_resolver_and_pass(command):
    # 허용 모양: `NAME=value` 접두 → rcm 낱말 → setup → 인자들, 메타 문자 없음, 절대 · ~ 경로
    assert judge(command, environ=TEST_XDG) is None, command
    # 시험 HOME 하나로도 지나간다(설정 폴더가 그 HOME 아래)
    test_home = (
        "HOME=/tmp/h rcm setup --role worker --yes --server http://h:1 --pool default "
        "--lanes 1 --label test.rcm.worker"
    )
    assert judge(test_home) is None


@pytest.mark.parametrize(
    "command",
    [
        "rcm setup --help && echo ok",
        "rcm setup --help > /tmp/help.txt",
        "rcm setup -h; rcm status",
        "rcm --no-follow setup --help",
        "env rcm setup --help",
    ],
)
def test_tr20_setup_help_passes_only_in_the_plain_shape(command):
    # `--help` 로 다른 것을 밀반입하지 못한다 — 도움말도 허용 모양일 때만 통과한다
    verdict = judge(command, environ=TEST_XDG)
    assert verdict is not None and verdict.decision == "deny", command


def _prod_with_links(tmp_path):
    real = tmp_path / "prod"
    for sub in ("config", "data", "checkout", "venv"):
        (real / sub).mkdir(parents=True)
    (tmp_path / "link").symlink_to(real, target_is_directory=True)
    (tmp_path / "free").mkdir()
    (tmp_path / "freelink").symlink_to(tmp_path / "free", target_is_directory=True)
    prod = guard.Production(
        checkout=real / "checkout",
        venv=real / "venv",
        config_dir=real / "config",
        data_dir=real / "data",
    )
    return prod, tmp_path / "link", tmp_path / "freelink"


@pytest.mark.parametrize(
    "flags",
    [
        "--data-dir {link}/data",
        "--data-dir {link}/data/inner/new",
        "--config {link}/config/server.toml --data-dir {free}/d",
        "--config {link}/checkout/x.toml --data-dir {free}/d",
        "--service-file {link}/venv/x.plist --data-dir {free}/d",
    ],
)
def test_tr20_a_symlink_into_a_production_root_is_denied(tmp_path, flags):
    # P3 4라운드 LOW 6: 경로는 존재하는 가장 깊은 조상까지 심볼릭 링크를 푼 뒤 견준다
    prod, link, free = _prod_with_links(tmp_path)
    base = f"{FREE_SERVER} --config {free}/s.toml --service-file {free}/s.plist"
    command = f"{base} {flags.format(link=link, free=free)}"
    verdict = judge(command, environ=TEST_XDG, prod=prod)
    assert verdict is not None and verdict.decision == "deny", flags


def test_tr20_a_symlink_to_a_free_folder_passes(tmp_path):
    prod, _link, free = _prod_with_links(tmp_path)
    command = (
        f"{FREE_SERVER} --config {free}/s.toml --service-file {free}/s.plist --data-dir {free}/d"
    )
    assert judge(command, environ=TEST_XDG, prod=prod) is None


# ── P3 3라운드 v2-1: 변이는 파이썬으로 읽히는 꼴이어야 한다 ──


def _mutcheck():
    path = ROOT / "scripts" / "mutcheck.py"
    spec = importlib.util.spec_from_file_location("mutcheck_for_setup", path)
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_every_python_mutant_applies_as_code_that_parses():
    # `guard-setup-service-place-allowed` 의 new 가 들여쓰기를 4칸 잃어 IndentationError 를 냈고,
    # mutcheck 는 그것도 「잡혔다」로 셌다. 모든 .py 변이를 적용해 ast.parse 한다
    import ast

    mc = _mutcheck()
    broken = []
    for m in mc.MUTANTS:
        if not m.path.endswith(".py"):
            continue
        text = (ROOT / m.path).read_text(encoding="utf-8")
        if text.count(m.old) != 1:
            continue  # 앵커 검사는 따로 있다
        try:
            ast.parse(text.replace(m.old, m.new))
        except SyntaxError as e:
            broken.append(f"{m.name}: {e.msg} (line {e.lineno})")
    assert broken == []


def test_mutcheck_reports_a_mutant_that_does_not_parse_as_a_failure(tmp_path, monkeypatch):
    # 문법 오류인 변이는 시험이 빨개져도 감지가 아니다 — 대조군을 돌리기 전에 실패로 보고한다
    mc = _mutcheck()
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "mod.py").write_text("def f():\n    if True:\n        return 1\n")
    monkeypatch.setattr(mc, "ROOT", root)
    ran = []
    monkeypatch.setattr(mc, "_pytest", lambda cmd, cwd, env=None: ran.append(cmd) or (1, 0.0, ""))
    bad = mc.Mutant(
        name="bad-indent",
        path="src/mod.py",
        old="    if True:\n        return 1\n",
        new="    if True:\n    return 1\n",
        tests=("tests/test_x.py",),
    )
    copy = tmp_path / "copy"
    copy.mkdir()
    ok, info = mc._run_in_copy(bad, copy, {})
    assert ok is False and "does not parse" in info, info
    assert ran == []


# ── P3 5라운드 G1: 인자를 만들어 넘기는 런처(xargs · parallel) 아래의 rcm ──


@pytest.mark.parametrize(
    "command",
    [
        "xargs rcm < /tmp/args.txt",
        "xargs -a /tmp/args.txt rcm",
        "cat /tmp/args.txt | xargs rcm",
        "cat /tmp/args.txt | xargs -n 1 rcm",
        "xargs -0 /abs/.venv/bin/rcm < /tmp/args.txt",
        "xargs --max-args=3 python3 -m remote_ci_monitor < /tmp/args.txt",
        "nice xargs rcm < /tmp/args.txt",
        "xargs env rcm < /tmp/args.txt",
        "xargs sh -c 'rcm --no-follow' _ < /tmp/args.txt",
        "xargs --weird-flag x rcm < /tmp/args.txt",
        "parallel rcm ::: a b",
        "parallel -j 2 /abs/bin/rcm :::: /tmp/args.txt",
    ],
)
def test_g1_rcm_fed_its_arguments_by_xargs_or_parallel_is_denied(command):
    # P3 5라운드 v1-1: 인자를 stdin · 파일 · 파이프가 주면 낱말 setup 이 글에 없어도 setup 에
    # 닿는다 — 운영 머신에서 rcm 을 부를 수 있는 xargs · parallel 은 막는다
    verdict = judge(command, environ=TEST_XDG)
    assert verdict is not None and verdict.decision == "deny", command
    assert PLAIN_MESSAGE in verdict.reason, command


@pytest.mark.parametrize(
    "command",
    [
        "find . -name '*.py' | xargs ./.venv/bin/ruff check",
        "xargs rm -f < /tmp/list",
    ],
)
def test_g1_xargs_that_runs_something_else_passes(command):
    # 과잉 차단의 경계: xargs 가 rcm 이 아닌 명령을 돌리고 글에 rcm 낱말이 없으면 조용하다. rcm 이
    # 그 명령의 인자인 칸(`xargs grep -n rcm`)은 batch 8 부터 막는다 — test_b8_1_an_rcm_word_…
    assert judge(command, environ=TEST_XDG) is None, command
    # 운영 설치가 없는 컴퓨터에서는 그물도 없다
    assert judge("xargs rcm < /tmp/a", prod=guard.Production()) is None


# ── P3 5라운드 G2: pty 런처 아래 하위 명령 없는 rcm(첫 실행 질문) ──


@pytest.mark.parametrize(
    "command",
    [
        "script -q /dev/null rcm",
        "script -q /tmp/log rcm --no-follow",
        "/usr/bin/script -q /dev/null /abs/.venv/bin/rcm",
        "script -c rcm /dev/null",
        "script -c 'rcm' /tmp/log",
        "script -c rcm status",
        "unbuffer rcm",
        "unbuffer python3 -m remote_ci_monitor",
        "expect -c 'spawn rcm; interact'",
        "socat - EXEC:rcm,pty,stderr",
        "nice script -q /dev/null rcm",
    ],
)
def test_g2_bare_rcm_under_a_pty_launcher_is_denied(command):
    # P3 5라운드 v1-2: 인자 없는 rcm 이 터미널을 받으면 첫 실행 질문이 setup 을 돌린다
    verdict = judge(command, environ=TEST_XDG)
    assert verdict is not None and verdict.decision == "deny", command
    assert PLAIN_MESSAGE in verdict.reason, command


@pytest.mark.parametrize(
    "command",
    [
        "script -q /tmp/log ls",
        "rcm",
    ],
)
def test_g2_rcm_with_a_subcommand_or_without_a_pty_passes(command):
    # pty 런처가 없거나(이 세션의 Bash 는 터미널이 아니다) rcm 낱말이 없으면 조용하다. 하위 명령이
    # 있는 rcm 을 pty 런처 아래 부르는 칸은 batch 8 부터 막는다 — test_b8_1_an_rcm_word_…
    assert judge(command, environ=TEST_XDG) is None, command


# ── P3 5라운드 G4: 감싸기를 경로로 써도 setup 이 아닌 규칙이 본다 ──


@pytest.mark.parametrize(
    "command",
    [
        "/usr/bin/env rcm token --config /home/op/.config/rcm/server.toml list",
        "/usr/bin/nohup rcm serve --config /home/op/.config/rcm/server.toml",
        "/usr/bin/nohup rcm serve --data-dir /home/op/.local/share/rcm",
        "/usr/bin/time rcm worker --config /home/op/.config/rcm/worker.toml",
    ],
)
def test_g4_a_wrapper_named_by_its_path_does_not_hide_rcm(command):
    # P3 5라운드 v2-1: `_strip_prefix` 는 감싸기를 이름(basename)으로 본다 — `/usr/bin/env` 도
    # `env` 다. setup 이 아닌 규칙(DB 를 여는 명령 · 서버)이 이것으로 잠긴다
    verdict = judge(command)
    assert verdict is not None and verdict.decision == "deny", command


# ── P3 5라운드 G3: 링크된 워크트리를 editable 로 설치한 서비스 ──


def _service_venv(root: Path, source: Path) -> Path:
    """`pyvenv.cfg` · `bin/rcm` · `source` 를 가리키는 editable `direct_url.json` 이 있는 venv."""
    (root / "bin").mkdir(parents=True)
    (root / "pyvenv.cfg").write_text("home = /usr/bin\n")
    launcher = root / "bin" / "rcm"
    launcher.write_text("#!/bin/sh\n")
    launcher.chmod(0o755)
    info = root / "lib" / "python3.11" / "site-packages" / "remote_ci_monitor-0.11.0.dist-info"
    info.mkdir(parents=True)
    (info / "direct_url.json").write_text(
        '{"dir_info": {"editable": true}, "url": "file://' + str(source) + '"}'
    )
    return root


def _linked_service_home(tmp_path: Path, monkeypatch, how: str) -> dict[str, Path]:
    """이 Mac 의 운영 꼴: 서비스가 실행하는 venv 의 editable 원본이 링크된 워크트리
    (`remote_ci_monitor-service`, `.git` 이 파일)다. venv 는 문서의 자리나 서비스 유닛이 적은
    자리에 있다."""
    home = tmp_path / "home"
    main = home / "src" / "remote_ci_monitor"
    (main / ".git" / "worktrees" / "service").mkdir(parents=True)
    service = home / "src" / "remote_ci_monitor-service"
    service.mkdir(parents=True)
    (service / ".git").write_text(f"gitdir: {main}/.git/worktrees/service\n")
    if how == "documented":
        venv = _service_venv(home / ".local" / "share" / "rcm-venv", service)
    else:
        venv = _service_venv(home / "venvs" / "rcm-service", service)
        unit = home / "Library" / "LaunchAgents" / "com.remote-ci-monitor.server.plist"
        unit.parent.mkdir(parents=True)
        unit.write_text(f"<array><string>{venv}/bin/rcm</string><string>serve</string></array>")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    return {"home": home, "service": service, "venv": venv}


@pytest.mark.parametrize("how", ["documented", "unit"])
def test_g3_a_service_venv_that_installs_a_linked_worktree_is_production(
    tmp_path, monkeypatch, how
):
    # P3 5라운드 v1-3: 서비스가 실행하는 venv 의 원본이 링크된 워크트리여도 그 워크트리가 운영
    # 체크아웃이다 — 편집 규칙과 setup 규칙이 같은 `production_root` 로 그것을 막는다
    fake = _linked_service_home(tmp_path, monkeypatch, how)
    prod = guard.find_production()
    assert prod.checkout == fake["service"] and prod.venv == fake["venv"], prod
    inside = str(fake["service"] / "src" / "x.py")
    edit = guard.decide("Edit", {"file_path": inside}, prod, tmp_path)
    assert edit is not None and edit.decision == "deny", edit
    setup_cmd = f"{FREE_SERVER} --data-dir /tmp/d --config {fake['service']}/s.toml"
    verdict = guard.decide("Bash", {"command": setup_cmd}, prod, tmp_path, environ=TEST_XDG)
    assert verdict is not None and verdict.decision == "deny", verdict
    assert "production checkout" in verdict.reason, verdict.reason


def test_g3_a_path_venv_that_installs_a_linked_worktree_is_still_not_production(
    tmp_path, monkeypatch
):
    # 경계: PATH 에서만 찾은 venv 는 서비스가 실행한다는 근거가 없다 — 원본이 링크된 워크트리면
    # 지금처럼 운영이 아니다(tests/test_guard_production.py 의 같은 꼴)
    fake = _linked_service_home(tmp_path, monkeypatch, "documented")
    elsewhere = fake["home"] / "venvs" / "dev"
    elsewhere.parent.mkdir(parents=True)
    fake["venv"].rename(elsewhere)
    monkeypatch.setenv("PATH", f"{elsewhere}/bin:/usr/bin:/bin")
    prod = guard.find_production()
    assert prod.checkout is None and prod.venv is None, prod


# ── P3 5라운드 G6: 변이 없던 허용 목록 규칙 ──


@pytest.mark.parametrize(
    "prefix", ["XDG_CONFIG_HOME=/tmp/a:~/b", "HOME=/tmp/h:~", "XDG_CONFIG_HOME=~/a:~/b"]
)
def test_g6_a_tilde_past_the_start_of_a_prefix_value_is_not_the_plain_shape(prefix):
    # 할당에서 셸은 `:` 뒤의 `~` 도 펼친다 — 훅은 맨 앞 `~` 만 펼치므로 그런 값은 허용 모양 밖이다
    verdict = judge(f"{prefix} {FREE_SERVER} --data-dir /tmp/d", environ=TEST_XDG)
    assert verdict is not None and verdict.decision == "deny", prefix
    assert PLAIN_MESSAGE in verdict.reason, prefix


@pytest.mark.parametrize(
    "flags",
    [
        "--data-dir /home/op/.local/share/rcm --data-dir /tmp/d --label test.rcm.server",
        "--data-dir /tmp/d --label rcm-server --label test.rcm.server",
        "--data-dir /tmp/d --label test.rcm.server --config=/home/op/.config/rcm/s.toml "
        "--config /tmp/s.toml",
    ],
)
def test_g6_an_earlier_value_of_a_repeated_flag_is_judged_too(flags):
    # argparse 는 마지막 값을 쓰지만 훅은 앞의 값도 본다(막는 쪽) — 앞에 운영 값이 있으면 막는다
    assert denied(f"{SERVER} {flags}", environ=TEST_XDG), flags


# ── P6 재확인(batch 8) 1: 런처의 그물은 낱말 자리에 기대지 않는다 ──


@pytest.mark.parametrize(
    "command",
    [
        "<f xargs rcm",
        "< /tmp/args.txt xargs rcm",
        "2>/dev/null xargs rcm < /tmp/args.txt",
        "2>&1 parallel rcm ::: a b",
        ">/dev/null script -q /dev/null rcm",
        "2>/dev/null unbuffer rcm",
        ">/tmp/log expect -c 'spawn rcm; interact'",
        r"find /tmp/a -name x -exec rcm {} \;",
        "find /tmp/a -execdir /abs/bin/rcm {} +",
        r"find /tmp/a -ok rcm {} \;",
        "fd x /tmp/a -x rcm",
        "fd x /tmp/a -X rcm",
        "fdfind x /tmp/a --exec rcm",
        "ls /tmp/a | entr rcm",
        "rush rcm < /tmp/args.txt",
        "xe rcm < /tmp/args.txt",
        "tmux new-session -d rcm",
        "screen -dm rcm",
        "ssh -t localhost rcm",
        "ssh -tt localhost /abs/bin/rcm",
        "ssh -o RequestTTY=force localhost rcm",
        "python3 -c 'import pty; pty.spawn([\"rcm\"])'",
        "python3 -c 'import pexpect; pexpect.spawn(\"rcm\").interact()'",
        "<f xargs $R",
        "<f xargs ./.venv/bin/r?m",
        "<f xargs ./.venv/bin/{r,x}cm",
    ],
)
def test_b8_1_a_launcher_word_anywhere_with_an_rcm_word_is_denied(command):
    # P6 재확인 1 HIGH: 앞에 붙은 리다이렉션(`<f` · `2>/dev/null` · `>/dev/null`)이 런처를 명령
    # 자리에서 밀어내도 그물이 본다 — 런처 낱말과 rcm 같은 낱말이 글 어디에든 있으면 막는다
    verdict = judge(command, environ=TEST_XDG)
    assert verdict is not None and verdict.decision == "deny", command
    assert PLAIN_MESSAGE in verdict.reason, command


@pytest.mark.parametrize(
    "command",
    [
        "xargs sh -c 'echo hi' _ < /tmp/a",
        "xargs python3 x.py < /tmp/a",
        "2>/dev/null xargs grep -n foo < /tmp/a",
        "find . -name '*.py' -exec ./.venv/bin/ruff check {} +",
        "find . -name '*.py' | xargs ./.venv/bin/ruff check",
        "fd -e py -x wc -l",
        "ssh host ls",
        "ssh host rcm status",
        "script -q /tmp/log ls",
        "tmux ls",
        "python3 -c 'import pty'",
    ],
)
def test_b8_1_a_launcher_without_an_rcm_word_passes(command):
    # 과잉 차단의 경계(v2-5): rcm 같은 낱말이 없으면(또는 pty 를 주지 않는 ssh 면) 조용하다
    assert judge(command, environ=TEST_XDG) is None, command


@pytest.mark.parametrize(
    "command",
    [
        "git ls-files | xargs grep -n rcm",
        "cat /tmp/a | xargs -n 1 echo rcm",
        "script -q /dev/null rcm status",
        "unbuffer rcm wait --job 5 --timeout 60",
        "script -c 'rcm status' /tmp/log",
        "expect -c 'spawn rcm status; interact'",
    ],
)
def test_b8_1_an_rcm_word_beside_a_launcher_is_denied_wherever_it_stands(command):
    # 자리를 보지 않으므로 rcm 이 다른 명령의 인자거나 하위 명령이 있어도 막는다(받아들인 과잉
    # 차단 — batch 7 의 G1 · G2 통과 칸이었다). 운영 설치가 없는 컴퓨터에서는 그물도 없다
    assert denied(command, environ=TEST_XDG), command
    assert judge(command, prod=guard.Production()) is None, command


# ── P6 재확인(batch 8) 2: 서비스가 체크아웃 안의 `.venv` 를 실행한다 ──


@pytest.mark.parametrize("linked", [True, False])
def test_b8_2_a_service_that_runs_the_venv_inside_its_checkout_is_production(
    tmp_path, monkeypatch, linked
):
    # v2-2: 설치된 유닛이 체크아웃 안의 `.venv/bin/rcm` 을 실행하면 그 venv 와 체크아웃이 운영이다
    # — 서비스로 도는 venv 는 자리와 상관없이 운영 뿌리다
    home = tmp_path / "home"
    checkout = home / "src" / "remote_ci_monitor-service"
    checkout.mkdir(parents=True)
    if linked:
        main = home / "src" / "remote_ci_monitor"
        (main / ".git" / "worktrees" / "service").mkdir(parents=True)
        (checkout / ".git").write_text(f"gitdir: {main}/.git/worktrees/service\n")
    else:
        (checkout / ".git").mkdir()
    venv = _service_venv(checkout / ".venv", checkout)
    unit = home / "Library" / "LaunchAgents" / "com.remote-ci-monitor.server.plist"
    unit.parent.mkdir(parents=True)
    unit.write_text(f"<array><string>{venv}/bin/rcm</string><string>serve</string></array>")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    prod = guard.find_production()
    assert prod.checkout == checkout and prod.venv == venv, prod
    edit = guard.decide("Edit", {"file_path": str(checkout / "src" / "x.py")}, prod, tmp_path)
    assert edit is not None and edit.decision == "deny", edit


def test_b8_2_a_developer_worktree_venv_on_path_is_still_free(tmp_path, monkeypatch):
    # 경계: 개발자 자신의 워크트리 `.venv`(PATH 에서만 찾은 것)는 운영이 아니다
    home = tmp_path / "home"
    worktree = home / "src" / "remote_ci_monitor-x"
    worktree.mkdir(parents=True)
    (worktree / ".git").mkdir()
    venv = _service_venv(worktree / ".venv", worktree)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", f"{venv}/bin:/usr/bin:/bin")
    prod = guard.find_production()
    assert prod.checkout is None and prod.venv is None, prod


# ── P6 재확인(batch 8) 7: nice · timeout · env -S 뒤의 rcm 도 setup 이 아닌 규칙이 본다 ──


@pytest.mark.parametrize(
    "command",
    [
        "nice rcm serve --data-dir /home/op/.local/share/rcm",
        "nice -n 5 rcm token --config /home/op/.config/rcm/server.toml list",
        "nice -n5 rcm serve --config /home/op/.config/rcm/server.toml",
        "nice --adjustment=5 rcm serve --config /home/op/.config/rcm/server.toml",
        "timeout 30 rcm serve --config /home/op/.config/rcm/server.toml",
        "timeout -s KILL 30s rcm worker --config /home/op/.config/rcm/worker.toml",
        "timeout --kill-after=5 1.5m rcm serve --data-dir /home/op/.local/share/rcm",
        "env -S 'rcm serve --config /home/op/.config/rcm/server.toml'",
        '/usr/bin/env -S "rcm token --config /home/op/.config/rcm/server.toml list"',
        "env --split-string='rcm serve --data-dir /home/op/.local/share/rcm'",
        "nice timeout 30 env -S 'rcm serve --data-dir /home/op/.local/share/rcm'",
    ],
)
def test_b8_7_nice_timeout_and_env_split_do_not_hide_rcm(command):
    # v2-7: `_strip_prefix` 가 nice(조정값), timeout(시간 하나와 그 플래그), env -S(나눌 글)를
    # 떼어 내야 뒤의 rcm 을 DB 를 여는 명령 · 서버 규칙이 본다
    verdict = judge(command)
    assert verdict is not None and verdict.decision == "deny", command


@pytest.mark.parametrize(
    "command",
    [
        "nice -n 5 make test",
        "timeout 30 ./.venv/bin/python -m pytest -q",
        "env -S 'python3 -c pass'",
    ],
)
def test_b8_7_nice_timeout_and_env_split_around_other_commands_pass(command):
    assert judge(command) is None, command


# ── P6 재확인(batch 9) 354: 훅의 과잉 차단 줄이기 ──


@pytest.mark.parametrize(
    "command",
    [
        "find src/remote_ci_monitor -name '*.py' -exec ruff check {} +",
        "find src/remote_ci_monitor/ -name '*.py' -exec ./.venv/bin/ruff check {} +",
        "git ls-files src/remote_ci_monitor | xargs ./.venv/bin/ruff check",
        "git ls-files -z src/remote_ci_monitor | xargs -0 -r ./.venv/bin/ruff check",
        'git ls-files -z tests | xargs -0 -r ./.venv/bin/ruff check "$PWD/x"',
        "echo $HOME | xargs ls",
        "grep -n expect src/remote_ci_monitor/x.py",
        "grep -rl foo src/remote_ci_monitor/ | xargs sed -n 1p",
    ],
)
def test_b9_354_a_path_fragment_or_an_argument_variable_beside_a_launcher_passes(command):
    # v2-1 · v2-2: `remote_ci_monitor` 가 경로 조각(앞뒤에 `/`)이면 rcm 낱말이 아니고, `$` · 백틱
    # 낱말은 런처 뒤에 있고 펼친 끝 조각이 글자 그대로의 다른 이름이 아닐 때만 rcm 일 수 있다
    assert judge(command, environ=TEST_XDG) is None, command


@pytest.mark.parametrize(
    "command",
    [
        "<f xargs $R",
        "xargs -0 -r $R < /tmp/a",
        "xargs -0 -r ./.venv/bin/$N < /tmp/a",
        'xargs -0 -r "$D/rcm" < /tmp/a',
        "find /tmp/a -exec `which r` {} +",
        "script -q /dev/null $R",
        "xargs python3 -m remote_ci_monitor.cli < /tmp/a",
        "xargs python3 -mremote_ci_monitor < /tmp/a",
        "xargs python3 -m remote_ci_monitor < /tmp/a",
    ],
)
def test_b9_354_a_variable_after_a_launcher_or_the_module_word_is_still_denied(command):
    # 줄이는 것은 경로 조각과 런처 앞 · 다른 이름으로 끝나는 변수뿐이다 — 런처가 실행할 자리의
    # 변수 · 정확한 모듈 낱말은 그대로 막는다
    verdict = judge(command, environ=TEST_XDG)
    assert verdict is not None and verdict.decision == "deny", command


@pytest.mark.parametrize(
    ("command", "launcher", "word"),
    [
        ("git ls-files | xargs grep -n rcm", "xargs", "rcm"),
        ("<f xargs $R", "xargs", "$R"),
        ("script -q /dev/null rcm status", "script", "rcm"),
    ],
)
def test_b9_354_the_launcher_deny_names_its_cause_and_says_split(command, launcher, word):
    # 거절이 원인(어떤 런처 + 어떤 낱말)과 「두 Bash 호출로 나눠라」를 말한다
    verdict = judge(command, environ=TEST_XDG)
    assert verdict is not None and verdict.decision == "deny", command
    assert f"`{launcher}`" in verdict.reason and f"`{word}`" in verdict.reason, verdict.reason
    assert "two Bash calls" in verdict.reason, verdict.reason
    assert PLAIN_MESSAGE in verdict.reason, verdict.reason


# ── P6 재확인(batch 9) v1-6 · v2-5: timeout 의 시간 `.5` · `1e1` · `inf` ──


@pytest.mark.parametrize("duration", [".5", "1e1", "inf", "INF", "infinity", "2.5e-1m", "1.s"])
def test_b9_timeout_durations_that_strtod_reads_are_stripped(duration):
    # GNU timeout 은 시간을 strtod 로 읽는다 — `.5` · `1e1` · `inf` 도 시간이다
    verdict = judge(f"timeout {duration} rcm serve --config /home/op/.config/rcm/server.toml")
    assert verdict is not None and verdict.decision == "deny", duration


# ── P6 재확인(batch 10) L3: 경로로 부른 cli.py 의 setup 은 훅이 통과시킨다(의도) ──


@pytest.mark.parametrize(
    "command",
    [
        f"python src/remote_ci_monitor/cli.py {SERVER.removeprefix('rcm ')}",
        f"./.venv/bin/python src/remote_ci_monitor/cli.py {WORKER.removeprefix('rcm ')}",
        f"python src/remote_ci_monitor/cli.py {SERVER.removeprefix('rcm ')} "
        "--config /home/op/.config/rcm/server.toml --force all",
    ],
)
def test_b10_l3_a_cli_py_path_run_of_setup_passes_the_hook_by_design(command):
    # 잠금: 결정 354 뒤로 `src/remote_ci_monitor/…` 같은 경로 조각은 rcm 낱말이 아니다. 그래서
    # `python src/remote_ci_monitor/cli.py setup …` 은 운영 설정을 줘도 훅을 지난다 — 결정 353 의
    # 「남는 위험」이고, 뒤는 setup 자신이 막는다(결정 352,
    # tests/test_setup.py::test_b9_352_setup_refuses_a_production_target_whatever_shell_built_it 의
    # config 칸). 이 시험이 빨개지면 훅이 다시 경로 조각을 세는 것이다 — 354 의 과잉 차단이다
    assert judge(command, environ=TEST_XDG) is None, command


# ── PR #274: 그물은 #272 그대로 — 터지면 거절하고, 우회 꼴과 과잉 차단을 잠근다 ──
#
# PR #274 는 #272 그물의 과잉 차단을 두 번 풀려 했다(글을 실행하는 명령의 열거, 다음에 좁은 면제).
# P3 격리 검증은 세 라운드 내리 면제가 글을 실행하는 무언가에 닿는 우회를 찾았다. 셸 명령 글의 허용
# 목록으로는 이 부류를 닫을 수 없다고 보고, 메인 세션이 Codex 와 논의해 선택지 B 를 골랐다
# (2026-11-26): 면제를 모두 거두고 #272 그물을 그대로 둔다. 운영 보호의 본체는 setup 자신의 거절이다
# (결정 352). 그물의 과잉 차단은 받아들인 비용이다 — 글은 파일로 넘긴다.


def _crashing_net(_command):
    raise RuntimeError("net-boom")


def test_pr274_a_crash_inside_the_net_counts_as_reaching_setup(monkeypatch):
    # PR #274 P3 LOW: `_about_setup` 은 `_setup_net` 이 터지면 False(setup 아님)를 돌려줬다 — 그러면
    # main() 은 경고만 남기고 명령을 허용한다. main() 의 fail-closed 주석과 어긋났다. 그물이 터진
    # Bash 명령은 setup 에 닿을 수 있는 쪽이다. 모양이 이상한 입력 · Bash 가 아닌 도구는 그대로
    # False 다
    monkeypatch.setattr(guard, "_setup_net", _crashing_net)
    assert guard._about_setup({"tool_name": "Bash", "tool_input": {"command": "ls"}}) is True
    assert guard._about_setup({"tool_name": "Read", "tool_input": {"file_path": "/x"}}) is False
    assert guard._about_setup({"tool_name": "Bash", "tool_input": "not a mapping"}) is False
    assert guard._about_setup("not a mapping") is False


def test_pr274_a_crash_inside_the_net_is_a_deny_from_the_hook(monkeypatch, capsys):
    # PR #274 P3 LOW: 그물이 터지면 훅 전체가 거절한다 — 경고만 남기고 허용하지 않는다
    import io
    import json

    monkeypatch.setattr(guard, "find_production", lambda: PROD)
    monkeypatch.setattr(guard, "_setup_net", _crashing_net)
    payload = {"tool_name": "Bash", "tool_input": {"command": "ls /tmp"}, "cwd": "/tmp"}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert guard.main() == 0
    out = json.loads(capsys.readouterr().out or "{}")
    assert out.get("hookSpecificOutput", {}).get("permissionDecision") == "deny", out
    assert "net-boom" in out["hookSpecificOutput"]["permissionDecisionReason"], out


#: P3 라운드들이 찾은 우회 꼴 — 면제가 있던 동안 지나갔거나 지나갈 뻔한 것. #272 그물은 모두 막는다
PR274_BYPASSES = [
    # 1라운드 HIGH F1: 실행자 목록에 없는 따옴표 글(trap · git 별칭 · 환경 변수 명령 · $SHELL ·
    # 파일에 써서 실행)
    "S=se; S=${S}tup; export S; trap 'rcm $S' EXIT; exit",
    "S=se; S=${S}tup; export S; git config alias.x '!rcm $S'; git x",
    "export S; GIT_SSH_COMMAND='rcm $S' git ls-remote ssh://h/x",
    "export S; git -c core.sshCommand='rcm $S' ls-remote ssh://h/x",
    "export S; echo 'rcm $S' | $SHELL",
    "printf 'rcm $S\\n' > /tmp/x.sh; chmod +x /tmp/x.sh; /tmp/x.sh",
    "printf 'rcm $S\\n' > /tmp/x.py; python3 /tmp/x.py",
    # 1라운드 HIGH F2: 명령 자리 모형이 놓친 자리(목록 밖 래퍼 · zsh 앞말 · coproc · 함수 · 변수)
    "R=r; R=${R}cm; echo | taskset 1 xargs $R $S",
    "echo | numactl -C 0 xargs $R $S",
    "echo | busybox xargs $R $S",
    "echo | strace -f xargs $R $S",
    "noglob xargs $R $S",
    "nocorrect xargs $R $S",
    "coproc xargs $R $S",
    "f() { xargs $R $S; }; echo | f",
    "R=r; R=${R}cm; F=find; $F . -exec $R $S \\;",
    'docker exec c sh -c "rcm $S"',
    # 1라운드 MED 1: `#` 주석 속 아포스트로피가 따옴표를 열어 진짜 줄을 따옴표 글로 만들었다
    "# don't\nrcm $S\n# won't",
    "echo a # it's\nrcm ${S}tup\n# 'x",
    "gh pr comment 1 --body #'\nrcm ${S}tup\n#'",
    # 1라운드 MED 4 · batch-2: 글이 실행될 파일로 흘러가는 꼴, 글 자리 밖의 rcm 글
    "echo 'rcm $S' && rcm status",
    "gh pr create --base 'rcm ${S}tup' --title x",
    "T='rcm $S'; git commit -m \"$T\"; echo $T",
    "git commit -m 'rcm ${S}tup' && git log -1 --format=%B | sh",
    "printf '%s' 'S=se; S=${S}tup; rcm $S' > /tmp/t.txt && cp /tmp/t.txt .git/hooks/pre-commit "
    "&& git commit -m x",
    "git commit -m 'S=se; S=${S}tup; rcm $S' && git log -1 --output=.git/hooks/post-commit "
    "&& git commit --allow-empty -m y",
    "git commit -m 'S=se; S=${S}tup; rcm $S' && git log -1 --format=%B > .git/hooks/post-commit",
    "cd /tmp/r && git commit -m 'S=se; S=${S}tup; rcm $S' && git log -1 --format=%B "
    "> /tmp/r/.git/hooks/post-commit && git commit --allow-empty -m y",
    "printf '%s' 'S=se; S=${S}tup; rcm $S' > /tmp/t.txt && find /tmp/t.txt -exec sh {} \\;",
    "git commit -m 'S=se; S=${S}tup; rcm $S' && S=$(git rev-parse --output=.git/hooks/x) "
    "&& git log -1 $S",
    "printf '%s' '--output=.git/hooks/x' > /tmp/a.txt && git commit -m 'rcm $S' "
    "&& git log -1 $(cat /tmp/a.txt)",
    "PATH=/tmp/e:$PATH; git commit -m 'rcm (x) 고친다'",
    "git commit -m 'fix rcm (x)' <<'EOF'\nEOF",
    # 2라운드 HIGH: 파일에 써 둔 글을 면제된 명령(`python3 tools/guard_naming.py`)이 실행한다
    "mkdir -p /tmp/x/tools && printf '%s' 'import os; os.system(\"rcm $(echo set)up\")' "
    "> /tmp/a.txt && cat /tmp/a.txt > /tmp/x/tools/guard_naming.py && cd /tmp/x "
    "&& python3 tools/guard_naming.py pr t",
    # 2라운드 HIGH: `$'…'` 가 find 의 플래그 검사를 숨긴다
    "printf '%s' 'S=se; S=${S}tup; rcm $S' > /tmp/t.txt && find /tmp/t.txt $'-exec' sh {} \\;",
]


@pytest.mark.parametrize("command", PR274_BYPASSES)
def test_pr274_the_bypass_shapes_the_p3_rounds_found_stay_denied(command):
    """잠금(처음부터 초록): PR #274 의 P3 라운드들이 찾은 우회 꼴을 #272 그물이 모두 막는다.

    누가 그물을 넓히거나 면제를 다시 얹으면 이 칸이 빨개진다. 그물을 바꾸려면 이 꼴마다 왜
    안전한지 먼저 보여야 한다 — 세 라운드 동안 면제가 이 부류(글을 실행하는 무언가에 닿는 꼴)를
    닫지 못했다."""
    verdict = judge(command, environ=TEST_XDG)
    assert verdict is not None and verdict.decision == "deny", command
    assert PLAIN_MESSAGE in verdict.reason, command


#: PR #272 머지 뒤 이 Mac 에서 막힌 평범한 명령(관찰 2026-11-26)과 그 이웃 — 낱말 setup 은 없다
PR274_ACCEPTED_OVER_BLOCKING = [
    # 관찰 1: 제목 변수와 `$(…)` · `tar -x` 가 한 명령에 있다
    "cd /home/op/src/remote_ci_monitor-x && H=/home/op/src/remote_ci_monitor-x/.verify/h && "
    "T='feat(cli): rcm wait 이 잡을 돌린 머신과 산출물 상태를 알려 준다 — --notify 로 알림' && "
    'python3 tools/guard_naming.py pr "$T" && GH_TOKEN=$(gh auth token --user u) gh pr create '
    '--base dev --title "$T" --body-file "$H/pr-body.md"; S=$(git rev-parse HEAD); '
    "mkdir -p $H/snap-1 && git archive $S | tar -x -C $H/snap-1",
    # 관찰 2: 제목을 파일로 넘기고 `"$(cat …)"` 로 읽는다 — 이 꼴도 막힌다
    "printf '%s' 'feat(cli): rcm wait 이 잡을 돌린 머신을 알려 준다' > /tmp/title.txt && "
    'python3 tools/guard_naming.py pr "$(cat /tmp/title.txt)"',
    # 관찰 3: 본문 안의 「rcm (…)」
    'gh pr comment 273 --body "verify-round: 3 · rcm/ci green · rcm (job 3712) 끝"',
    'T="docs(wait): rcm wait 안내"; gh pr create --title "$T" --body-file "$H/pr.md"',
    'git commit -m \'fix(cli): rcm wait 이 (#273) 을 고친다\' && git log -1 "$A" "$B"',
    # 인자 자리의 런처 낱말(`-x` · `expect` · `xargs` · `-exec`)과 rcm 이 한 명령에 있다
    "fd -e py src | wc -l; git archive HEAD | tar -x -C /tmp/snap; rcm status",
    "fd -e md docs > /tmp/l; unzip -x a.zip b; rcm wait --job 5 --timeout 60",
    "grep -x foo /tmp/a && fd -e md; rcm status",
    "grep -n expect docs/operating.md; rcm status",
    "git log --grep xargs -1 && rcm status",
    "find . -name '*.md' > /tmp/l; grep -n -- -exec /tmp/l; rcm status",
]


@pytest.mark.parametrize("command", PR274_ACCEPTED_OVER_BLOCKING)
def test_pr274_the_over_blocking_is_an_accepted_cost(command):
    """잠금 — 받아들인 비용(PR #274, 선택지 B, 2026-11-26): 이 명령들은 setup 과 상관없지만 #272
    그물이 막는다.

    두 번의 면제 시도가 P3 에서 우회를 남겨 거두었다(결정: 면제 없음, 운영 보호의 본체는 setup
    자신의 거절 — 결정 352). 비켜 가는 길은 글을 파일로 넘기는 것이다: `gh pr … --body-file`,
    `git commit -F <파일>`, 그리고 rcm 낱말이 없는 명령. 그물을 넓혀 이 칸을 허용으로 바꾸려면
    test_pr274_the_bypass_shapes_the_p3_rounds_found_stay_denied 의 모든 꼴이 계속 막힘을 먼저
    보여야 한다."""
    verdict = judge(command, environ=TEST_XDG)
    assert verdict is not None and verdict.decision == "deny", command
