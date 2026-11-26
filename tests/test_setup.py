"""`rcm setup` 의 몸통 — `remote_ci_monitor.setup.run` 과 순수 `core/setup.py` 를 바로 부른다
(M5t-a 결정 239~242, docs/m5t-workplan.md §4.1 · §7 TR).

운영 보호: 모든 시험이 임시 HOME · XDG_CONFIG_HOME · XDG_DATA_HOME 과 시험 라벨을 쓴다. 바깥 세계
(TTY · 인터페이스 주소 · 포트 · 서비스 관리자 조회 · 호스트 사실 · rcm 실행 파일)는 가짜 `World`
로 바꿔 끼운다 — 진짜 `launchctl` · `systemctl` 은 부르지 않는다.
"""

from __future__ import annotations

import io
import os
import plistlib
import shutil
import socket
import sqlite3
import subprocess
import tomllib
from pathlib import Path

import pytest

from remote_ci_monitor import setup
from remote_ci_monitor.core import setup as core
from remote_ci_monitor.store import DB_VERSION

RCM = Path("/opt/rcm-test/venv/bin/rcm")
LAN = "192.168.77.20"
TAILNET = "100.101.102.103"
SERVER_LABEL = "com.remote-ci-monitor.server"
WORKER_LABEL = "com.remote-ci-monitor.worker"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """임시 HOME · XDG 와 셸의 `RCM_*` 없음. 작업 폴더도 임시 폴더다(`./rcm.toml` 을 줍지 않게)."""
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    for name in (
        "RCM_CONFIG",
        "RCM_SERVER",
        "RCM_TOKEN",
        "RCM_WORKER_TOKEN",
        "RCM_SERVER_DATA_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    return h


@pytest.fixture
def cfg(home, tmp_path):
    return tmp_path / "xdg-config" / "rcm"


class Fake:
    """가짜 바깥 세계. `calls` 에 서비스 관리자 조회를 모은다."""

    def __init__(self, **kw):
        self.out, self.err = io.StringIO(), io.StringIO()
        self.calls: list[tuple[str, str]] = []
        self.facts_called = 0
        local = set(kw.pop("local", (LAN, "127.0.0.1", "0.0.0.0")))
        loaded = kw.pop("loaded", False)
        rcm = kw.pop("rcm", (RCM, False))
        answers = kw.pop("answers", None)

        def _loaded(manager, label):
            self.calls.append((manager, label))
            return loaded

        def _facts():
            self.facts_called += 1
            return {"os": "darwin", "arch": "arm64", "package_manager": "brew", "root": False}

        fields = {
            "platform": "darwin",
            "stdin": io.StringIO("" if answers is None else "\n".join(answers) + "\n"),
            "stdout": self.out,
            "stderr": self.err,
            "interactive": lambda: answers is not None,
            "addresses": lambda: [LAN],
            "is_local_address": lambda a: a in local,
            "port_in_use": lambda host, port: False,
            "service_loaded": _loaded,
            "host_facts": _facts,
            "find_rcm": lambda: rcm,
        }
        fields.update(kw)
        self.world = setup.World(**fields)

    @property
    def text(self) -> str:
        return self.out.getvalue() + self.err.getvalue()


def go(fake: Fake, *argv: str) -> int:
    return setup.run(setup.build_parser().parse_args(list(argv)), fake.world)


def coordinator_args(data: Path, *extra: str, role: str = "coordinator-worker") -> list[str]:
    return [
        "--role",
        role,
        "--bind",
        "127.0.0.1",
        "--port",
        "18790",
        "--data-dir",
        str(data),
        "--label",
        "test.rcm.server",
        "--no-advertise",
        "--yes",
        *extra,
    ]


def read_toml(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def make_db(path: Path, user_version: int) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE t (x)")
    con.execute(f"PRAGMA user_version = {user_version}")
    con.commit()
    con.close()
    return path.read_bytes()


# ── 역할 읽어 내기 (결정 239) ────────────────────────────────────────────────


def _server(path: Path, lanes: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"[server]\nport = 18790\nlanes = {lanes}\n", encoding="utf-8")


def test_tr4_roles_come_from_the_config_files_that_exist(cfg, tmp_path, monkeypatch):
    assert setup.existing_roles(os.environ) == []
    _server(cfg / "server.toml", 1)
    assert setup.existing_roles(os.environ) == ["coordinator-worker"]
    _server(cfg / "server.toml", 0)
    assert setup.existing_roles(os.environ) == ["coordinator"]
    (cfg / "worker.toml").write_text('server = "http://h:1"\n', encoding="utf-8")
    (cfg / "client.toml").write_text('server = "http://h:1"\n', encoding="utf-8")
    assert setup.existing_roles(os.environ) == ["coordinator", "worker", "client"]


def test_tr4_rcm_config_and_a_local_rcm_toml_are_read_as_server_configs(
    home, tmp_path, monkeypatch
):
    elsewhere = tmp_path / "elsewhere.toml"
    _server(elsewhere, 0)
    monkeypatch.setenv("RCM_CONFIG", str(elsewhere))
    assert setup.existing_roles(os.environ) == ["coordinator"]
    monkeypatch.delenv("RCM_CONFIG")
    _server(tmp_path / "rcm.toml", 2)
    assert setup.existing_roles(os.environ) == ["coordinator-worker"]


def test_tr38_a_missing_rcm_config_is_a_usage_error(cfg, tmp_path, monkeypatch):
    monkeypatch.setenv("RCM_CONFIG", str(tmp_path / "gone.toml"))
    with pytest.raises(setup.SetupUsage):
        setup.existing_roles(os.environ)
    fake = Fake()
    assert go(fake, *coordinator_args(tmp_path / "data")) == 2
    assert "RCM_CONFIG" in fake.err.getvalue()
    assert not (cfg / "server.toml").exists()


# ── 쓰기: --yes · 덮지 않음 · 반쪽 없음 · 종료 코드 ─────────────────────────────


def test_tr5_coordinator_worker_with_yes_writes_server_toml_with_lanes_and_no_worker_toml(
    cfg, tmp_path
):
    fake = Fake()
    assert go(fake, *coordinator_args(tmp_path / "data")) == 0
    server = read_toml(cfg / "server.toml")["server"]
    assert server["lanes"] >= 1
    assert not (cfg / "worker.toml").exists()
    assert fake.world.stdin.read() == ""  # 아무것도 묻지 않았다


def test_tr5_the_coordinator_role_writes_lanes_zero(cfg, tmp_path):
    fake = Fake()
    assert go(fake, *coordinator_args(tmp_path / "data", role="coordinator")) == 0
    assert read_toml(cfg / "server.toml")["server"]["lanes"] == 0


def test_tr6_an_existing_file_is_kept_and_only_the_forced_kind_is_replaced(cfg, tmp_path):
    cfg.mkdir(parents=True)
    (cfg / "server.toml").write_text("old = 1\n", encoding="utf-8")
    fake = Fake()
    assert go(fake, *coordinator_args(tmp_path / "data")) == 1
    assert (cfg / "server.toml").read_text(encoding="utf-8") == "old = 1\n"
    assert "--force" in fake.err.getvalue()
    # 서비스 파일만 덮으라고 했다 — 설정 파일은 그대로다
    assert go(Fake(), *coordinator_args(tmp_path / "data", "--force", "service")) == 1
    assert (cfg / "server.toml").read_text(encoding="utf-8") == "old = 1\n"
    assert go(Fake(), *coordinator_args(tmp_path / "data", "--force", "config")) == 0
    assert read_toml(cfg / "server.toml")["server"]["port"] == 18790


def test_tr6_an_existing_service_file_is_kept_without_force_and_nothing_else_is_written(
    cfg, tmp_path
):
    staged = cfg / "service" / "test.rcm.server.plist"
    staged.parent.mkdir(parents=True)
    staged.write_bytes(b"mine")
    assert go(Fake(), *coordinator_args(tmp_path / "data")) == 1
    assert staged.read_bytes() == b"mine"
    assert not (cfg / "server.toml").exists()  # 하나라도 못 쓰면 아무것도 쓰지 않는다
    assert go(Fake(), *coordinator_args(tmp_path / "data", "--force", "service")) == 0
    assert staged.read_bytes() != b"mine"


def test_tr7_a_failure_part_way_leaves_no_half_written_config(cfg, tmp_path):
    cfg.mkdir(parents=True)
    (cfg / "service").write_text("a file where the service folder goes", encoding="utf-8")
    fake = Fake()
    assert go(fake, *coordinator_args(tmp_path / "data")) == 1
    assert not (cfg / "server.toml").exists()
    # 덮으라고 한 설정 파일은 옛 내용으로 돌아온다
    (cfg / "server.toml").write_text("old = 1\n", encoding="utf-8")
    assert go(Fake(), *coordinator_args(tmp_path / "data", "--force")) == 1
    assert (cfg / "server.toml").read_text(encoding="utf-8") == "old = 1\n"


@pytest.mark.parametrize(
    ("argv", "code"),
    [
        (["--force", "config"], 0),  # 썼다
        ([], 1),  # 있는 파일 — 쓰지 못했다
        (["--port", "70000"], 2),  # 사용법
    ],
)
def test_tr28_exit_codes(cfg, tmp_path, argv, code):
    cfg.mkdir(parents=True)
    (cfg / "server.toml").write_text("old = 1\n", encoding="utf-8")
    assert go(Fake(), *coordinator_args(tmp_path / "data"), *argv) == code


# ── 토큰 · Store ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "argv",
    [
        ["--role", "client", "--server", "http://10.0.0.5:18790", "--yes"],
        [
            "--role",
            "worker",
            "--server",
            "http://10.0.0.5:18790",
            "--pool",
            "p1",
            "--lanes",
            "2",
            "--label",
            "test.rcm.worker",
            "--yes",
        ],
        None,
    ],
)
def test_tr8_tokens_in_the_shell_are_never_repeated(cfg, tmp_path, monkeypatch, argv):
    client_token, worker_token = "tok-" + "Q7x" * 6, "wtok-" + "Z9y" * 6
    monkeypatch.setenv("RCM_TOKEN", client_token)
    monkeypatch.setenv("RCM_WORKER_TOKEN", worker_token)
    fake = Fake()
    assert go(fake, *(argv or coordinator_args(tmp_path / "data"))) == 0
    assert fake.text.strip()
    assert client_token not in fake.text and worker_token not in fake.text
    for p in cfg.rglob("*"):
        if p.is_file():
            body = p.read_bytes()
            assert client_token.encode() not in body and worker_token.encode() not in body


def test_tr9_setup_opens_no_store_even_on_a_data_dir_with_a_database(cfg, tmp_path, monkeypatch):
    db = tmp_path / "data" / "rcm.sqlite3"
    before = make_db(db, 7)

    def refuse(*a, **k):
        raise AssertionError("setup opened a database")

    import remote_ci_monitor.store as store

    monkeypatch.setattr(sqlite3, "connect", refuse)
    monkeypatch.setattr(store.Store, "__init__", refuse)
    fake = Fake()
    assert go(fake, *coordinator_args(tmp_path / "data", "--reuse-data-dir")) == 0
    assert read_toml(cfg / "server.toml")["server"]["data_dir"] == str(tmp_path / "data")
    assert db.read_bytes() == before
    assert not db.with_name("rcm.sqlite3-wal").exists()
    assert not db.with_name("rcm.sqlite3-shm").exists()


# ── bind (결정 241 · Q10 · Q14) ──────────────────────────────────────────────


def test_tr10_a_tailscale_address_is_the_recommended_bind():
    choices = core.bind_choices([LAN, TAILNET])
    assert choices[0] == (TAILNET, "tailscale")
    assert ("0.0.0.0", "all") not in choices  # Tailscale 이 있으면 LAN 을 권하지 않는다


def test_tr10_without_tailscale_the_choices_are_the_lan_address_or_all(cfg):
    choices = core.bind_choices([LAN])
    assert (LAN, "lan") in choices and ("0.0.0.0", "all") in choices
    lan, every = " ".join(core.bind_warnings("lan")), " ".join(core.bind_warnings("all"))
    assert "anyone on this network can read" in lan and "DHCP" in lan
    assert "anyone on this network can read" in every
    for kind in ("lan", "all", "tailscale", "loopback"):
        # 없는 읽기 인증 모드를 권하지 않는다(Q14)
        assert all("read_auth" not in w for w in core.bind_warnings(kind))


def test_tr10_choosing_lan_or_all_warns_that_reads_are_open(cfg, tmp_path):
    for bind in (LAN, "0.0.0.0"):
        fake = Fake()
        argv = coordinator_args(tmp_path / "data", "--force")
        argv[argv.index("--bind") + 1] = bind
        assert go(fake, *argv) == 0
        assert "anyone on this network can read" in fake.err.getvalue()


def test_tr10_a_typed_address_must_belong_to_this_machine(cfg, tmp_path):
    fake = Fake()
    argv = coordinator_args(tmp_path / "data")
    argv[argv.index("--bind") + 1] = "10.9.9.9"
    assert go(fake, *argv) == 2
    assert "not an address of this machine" in fake.err.getvalue()
    assert not (cfg / "server.toml").exists()
    # 대화형: 이 머신에 없는 주소는 다시 묻는다
    answers = ["10.9.9.9", LAN, "", "", "", ""]
    fake = Fake(answers=answers)
    assert go(fake, "--role", "coordinator-worker") == 0
    assert "not an address of this machine" in fake.err.getvalue()
    assert read_toml(cfg / "server.toml")["server"]["bind"] == LAN


def test_tr25_a_tailscale_bind_writes_advertise_false_and_warns_about_the_tailnet(cfg, tmp_path):
    fake = Fake(local=(TAILNET,))
    argv = [
        "--role",
        "coordinator-worker",
        "--bind",
        TAILNET,
        "--data-dir",
        str(tmp_path / "data"),
        "--label",
        "test.rcm.server",
        "--yes",
    ]
    assert go(fake, *argv) == 0
    assert read_toml(cfg / "server.toml")["server"]["advertise"] is False
    assert "everyone on your tailnet can read" in fake.err.getvalue()


def test_tr23_yes_on_a_coordinator_role_needs_bind(cfg, tmp_path):
    for role in ("coordinator", "coordinator-worker"):
        fake = Fake()
        argv = [a for a in coordinator_args(tmp_path / "data", role=role)]
        i = argv.index("--bind")
        del argv[i : i + 2]
        assert go(fake, *argv) == 2
        assert "--bind" in fake.err.getvalue()
        assert not (cfg / "server.toml").exists()
    assert core.missing_for_yes("coordinator", {"bind": None}) == ["--bind"]


@pytest.mark.parametrize(
    ("role", "given", "missing"),
    [
        ("client", {}, ["--server"]),
        ("worker", {"pool": "p", "lanes": 1}, ["--server"]),
        ("worker", {"server": "http://h:1", "lanes": 1}, ["--pool"]),
        ("worker", {"server": "http://h:1", "pool": "p"}, ["--lanes"]),
    ],
)
def test_tr35_yes_without_a_required_flag_is_a_usage_error(cfg, role, given, missing):
    assert core.missing_for_yes(role, given) == missing
    argv = ["--role", role, "--yes"]
    for key, value in given.items():
        argv += [f"--{key}", str(value)]
    fake = Fake()
    assert go(fake, *argv) == 2
    assert missing[0] in fake.err.getvalue()
    assert not cfg.exists() or not any(cfg.glob("*.toml"))


# ── 서비스 파일 (결정 241 · 6-B-V1 · V7) ─────────────────────────────────────


def test_tr11_the_launchd_server_file_has_the_required_keys():
    home = Path("/Users/someone")
    doc = plistlib.loads(
        core.server_plist(
            rcm=RCM,
            config=home / ".config/rcm/server.toml",
            home=home,
            label="test.rcm.server",
            log=home / "Library/Logs/rcm/test.rcm.server.log",
            path="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        )
    )
    assert doc["SoftResourceLimits"]["NumberOfFiles"] == 4096
    assert doc["EnvironmentVariables"]["HOME"] == str(home)
    assert doc["WorkingDirectory"] == str(home)
    assert doc["StandardOutPath"] and doc["StandardErrorPath"]
    path = doc["EnvironmentVariables"]["PATH"].split(":")
    assert "/usr/sbin" in path and path[0] != str(RCM.parent)
    assert doc["ProgramArguments"][:2] == [str(RCM), "serve"]


def test_tr11_the_systemd_server_unit_has_the_required_keys():
    home = Path("/home/someone")
    text = core.server_unit(
        rcm=RCM,
        config=home / ".config/rcm/server.toml",
        home=home,
        label="test.rcm.server",
        log=home / ".local/state/rcm/test.rcm.server.log",
        path="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
    ).decode()
    lines = text.splitlines()
    assert "LimitNOFILE=4096" in lines
    assert f"Environment=HOME={home}" in lines
    assert f"WorkingDirectory={home}" in lines
    assert any(line.startswith("StandardOutput=append:") for line in lines)
    path_line = next(line for line in lines if line.startswith("Environment=PATH="))
    entries = path_line.split("=", 2)[2].split(":")
    assert "/usr/sbin" in entries and entries[0] != str(RCM.parent)


def test_tr11_doctor_reads_the_written_server_file_with_its_fd_limit(cfg, tmp_path):
    from remote_ci_monitor import doctor

    assert go(Fake(), *coordinator_args(tmp_path / "data")) == 0
    sf = doctor.read_service_file(cfg / "service" / "test.rcm.server.plist")
    assert sf.nofile == 4096 and sf.command == "serve"


def test_tr12_setup_registers_and_starts_nothing_it_only_prints_the_commands(
    cfg, tmp_path, monkeypatch
):
    ran: list[list[str]] = []

    def record(argv, *a, **k):
        ran.append(list(argv))
        raise AssertionError(f"setup ran {argv}")

    monkeypatch.setattr(subprocess, "run", record)
    monkeypatch.setattr(subprocess, "Popen", record)
    fake = Fake()
    assert go(fake, *coordinator_args(tmp_path / "data")) == 0
    assert ran == []
    assert "launchctl bootstrap" in fake.err.getvalue()
    assert not (Path(os.environ["HOME"]) / "Library" / "LaunchAgents").exists()


def test_tr13_the_last_step_runs_only_the_checks_that_need_no_server(cfg, tmp_path, monkeypatch):
    import remote_ci_monitor.client as client

    def no_server(*a, **k):
        raise AssertionError("setup talked to a server")

    monkeypatch.setattr(client.Client, "__init__", no_server)
    fake = Fake()
    assert go(fake, *coordinator_args(tmp_path / "data")) == 0
    err = fake.err.getvalue()
    assert fake.facts_called == 1  # 호스트 사실
    assert "python" in err and "sqlite" in err  # rcm 자신의 요구
    assert "open files" in err and "4096" in err  # 쓴 서비스 파일에서 읽은 상한
    assert "config" in err  # 설정 파일 검증
    assert "advice" in err
    last = err.strip().splitlines()[-1]
    assert "rcm doctor --role coordinator-worker" in last
    assert "start the service" in last and "token" in last


def test_tr15_a_worker_toml_next_to_a_coordinator_worker_setup_is_only_a_warning(cfg, tmp_path):
    cfg.mkdir(parents=True)
    (cfg / "worker.toml").write_text('server = "http://h:1"\n', encoding="utf-8")
    fake = Fake()
    assert go(fake, *coordinator_args(tmp_path / "data")) == 0
    assert "worker.toml" in fake.err.getvalue() and "warning" in fake.err.getvalue()
    assert (cfg / "server.toml").exists()


def test_tr18_no_label_means_the_standard_label_and_a_loaded_one_is_only_a_warning(cfg, tmp_path):
    argv = coordinator_args(tmp_path / "data")
    i = argv.index("--label")
    del argv[i : i + 2]
    fake = Fake(loaded=True)
    assert go(fake, *argv) == 0
    staged = cfg / "service" / f"{SERVER_LABEL}.plist"
    assert plistlib.loads(staged.read_bytes())["Label"] == SERVER_LABEL
    assert fake.calls == [("launchd", SERVER_LABEL)]
    assert "already loaded" in fake.err.getvalue()
    # 같은 라벨의 파일이 있으면 --force 없이 덮지 않는다(운영 라벨이라는 이유로 거절하지는 않는다)
    staged.write_bytes(b"kept")
    assert go(Fake(), *argv, "--force", "config") == 1
    assert staged.read_bytes() == b"kept"


def test_tr21_tr27_a_coordinator_setup_prints_a_client_and_a_worker_token_command(cfg, tmp_path):
    fake = Fake()
    assert go(fake, *coordinator_args(tmp_path / "data")) == 0
    err = fake.err.getvalue()
    config = cfg / "server.toml"
    lines = [line for line in err.splitlines() if f"token --config {config} add" in line]
    assert any("--worker" not in line for line in lines)  # client 권한
    assert any(line.rstrip().endswith("--worker") for line in lines)
    assert all("--admin" not in line for line in lines)
    assert all(str(RCM) in line for line in lines)  # 서비스와 같은 빌드로 만든다


def test_tr22_flags_land_in_server_toml_and_the_service_file(cfg, tmp_path):
    fake = Fake()
    argv = [
        "--role",
        "coordinator-worker",
        "--bind",
        LAN,
        "--port",
        "18792",
        "--data-dir",
        "rel/data",
        "--advertise",
        "--label",
        "test.rcm.flags",
        "--yes",
    ]
    assert go(fake, *argv) == 0
    server = read_toml(cfg / "server.toml")["server"]
    assert server["bind"] == LAN and server["port"] == 18792
    assert server["data_dir"] == str(tmp_path / "rel" / "data")  # 절대 경로
    assert server["advertise"] is True
    doc = plistlib.loads((cfg / "service" / "test.rcm.flags.plist").read_bytes())
    assert doc["Label"] == "test.rcm.flags"
    assert doc["ProgramArguments"][-1] == str(cfg / "server.toml")


def test_tr22_the_same_values_are_asked_in_a_terminal_with_template_defaults(cfg, home, tmp_path):
    # bind 고르기(1 = LAN), port, data_dir, advertise, label — 빈 답은 기본값이다
    fake = Fake(answers=["1", "", "", "", ""])
    assert go(fake, "--role", "coordinator-worker") == 0
    server = read_toml(cfg / "server.toml")["server"]
    assert server["bind"] == LAN and server["port"] == 8787
    assert server["data_dir"] == str(home / ".local" / "share" / "rcm")
    assert (cfg / "service" / f"{SERVER_LABEL}.plist").exists()
    prompts = fake.err.getvalue()
    for word in ("port", "data_dir", "advertise", "label"):
        assert word in prompts
    fake = Fake(answers=["2", "18793", "mine", "n", "test.rcm.asked"])
    assert go(fake, "--role", "coordinator-worker", "--force") == 0
    server = read_toml(cfg / "server.toml")["server"]
    assert server["bind"] == "0.0.0.0" and server["port"] == 18793
    assert server["data_dir"] == str(tmp_path / "mine") and server["advertise"] is False
    assert (cfg / "service" / "test.rcm.asked.plist").exists()


def test_tr24_the_service_file_goes_under_the_config_folder_not_launch_agents(cfg, home, tmp_path):
    fake = Fake()
    assert go(fake, *coordinator_args(tmp_path / "data")) == 0
    staged = cfg / "service" / "test.rcm.server.plist"
    assert staged.is_file()
    agents = home / "Library" / "LaunchAgents"
    assert not agents.exists()
    err = fake.err.getvalue()
    assert f"cp -n {staged} {agents / 'test.rcm.server.plist'}" in err
    assert f"launchctl bootstrap gui/$(id -u) {agents / 'test.rcm.server.plist'}" in err
    other = tmp_path / "elsewhere" / "svc.plist"
    assert (
        go(Fake(), *coordinator_args(tmp_path / "data", "--force", "--service-file", str(other)))
        == 0
    )
    assert other.is_file()


def test_tr24_on_linux_the_unit_is_staged_under_its_unit_name(cfg, home, tmp_path):
    fake = Fake(platform="linux")
    assert go(fake, *coordinator_args(tmp_path / "data")) == 0
    staged = cfg / "service" / "test.rcm.server.service"
    assert staged.is_file()
    err = fake.err.getvalue()
    assert f"cp -n {staged} {home / '.config/systemd/user/test.rcm.server.service'}" in err
    assert "systemctl --user enable --now test.rcm.server.service" in err
    assert fake.calls == [("systemd", "test.rcm.server")]


def test_tr26_a_worker_setup_writes_the_coordinators_url_never_auto(cfg, tmp_path):
    argv = ["--role", "worker", "--pool", "p1", "--lanes", "2", "--label", "test.rcm.worker"]
    fake = Fake()
    assert go(fake, *argv, "--server", "http://10.0.0.5:18790/", "--yes") == 0
    assert read_toml(cfg / "worker.toml")["server"] == "http://10.0.0.5:18790"
    doc = plistlib.loads((cfg / "service" / "test.rcm.worker.plist").read_bytes())
    assert doc["Label"] == "test.rcm.worker"
    assert doc["ProgramArguments"][1:3] == ["worker", "--config"]
    fake = Fake()
    assert go(fake, *argv, "--server", "auto", "--yes", "--force") == 2
    assert "auto" in fake.err.getvalue()


def test_tr31_a_data_dir_that_already_holds_a_database_is_refused_unless_reused(cfg, tmp_path):
    db = tmp_path / "data" / "rcm.sqlite3"
    make_db(db, DB_VERSION - 3)
    fake = Fake()
    assert go(fake, *coordinator_args(tmp_path / "data")) == 1
    err = fake.err.getvalue()
    assert "already holds an rcm database" in err and "--reuse-data-dir" in err
    assert not (cfg / "server.toml").exists()
    fake = Fake()
    assert go(fake, *coordinator_args(tmp_path / "data", "--reuse-data-dir")) == 0
    assert read_toml(cfg / "server.toml")["server"]["data_dir"] == str(tmp_path / "data")
    # 6-B-V3: 헤더의 user_version 과 이 빌드의 DB_VERSION 이 다르면 알린다(DB 는 열지 않는다)
    assert setup.db_header_version(db) == DB_VERSION - 3
    assert f"v{DB_VERSION - 3}" in fake.err.getvalue() and f"v{DB_VERSION}" in fake.err.getvalue()


def test_tr32_a_port_in_use_is_a_warning(cfg, tmp_path):
    held = socket.socket()
    held.bind(("127.0.0.1", 0))
    held.listen(1)
    try:
        port = held.getsockname()[1]
        assert setup.port_in_use("127.0.0.1", port) is True
    finally:
        held.close()
    assert setup.port_in_use("127.0.0.1", port) is False
    fake = Fake(port_in_use=lambda host, p: True)
    assert go(fake, *coordinator_args(tmp_path / "data")) == 0
    assert "port 18790 is in use" in fake.err.getvalue()


def test_tr34_the_server_file_runs_the_build_that_ran_setup(cfg, tmp_path):
    build = Path("/wt/remote_ci_monitor-x/.venv/bin/rcm")
    fake = Fake(rcm=(build, False))
    assert go(fake, *coordinator_args(tmp_path / "data")) == 0
    doc = plistlib.loads((cfg / "service" / "test.rcm.server.plist").read_bytes())
    assert doc["ProgramArguments"][0] == str(build)
    assert "found on PATH" not in fake.err.getvalue()
    # 6-B-V6: 돌린 빌드를 못 찾고 PATH 의 rcm 으로 떨어졌으면 그렇다고 알린다
    fake = Fake(rcm=(build, True))
    assert go(fake, *coordinator_args(tmp_path / "data", "--force")) == 0
    assert "found on PATH" in fake.err.getvalue()


def test_tr34_find_rcm_prefers_the_running_executable(tmp_path, monkeypatch):
    exe = tmp_path / "bin" / "rcm"
    exe.parent.mkdir()
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr("sys.argv", [str(exe)])
    assert setup.find_rcm() == (exe, False)


def test_tr36_the_copy_command_never_overwrites_and_bootstrap_waits_for_an_equal_file(tmp_path):
    staged, installed = tmp_path / "s.plist", tmp_path / "LaunchAgents" / "s.plist"
    for manager in ("launchd", "systemd"):
        lines = core.install_commands(
            manager, staged=staged, installed=installed, label="s", log=tmp_path / "s.log"
        )
        assert f"cp -n {staged} {installed}" in lines
        # 6-B-U2: `cp -n` 의 종료 코드는 macOS 와 GNU 가 다르다 — 그 코드에 기대지 않는다
        assert not any("cp -n" in line and "&&" in line for line in lines)
        start = next(line for line in lines if "bootstrap" in line or "enable --now" in line)
        assert start.startswith(f"cmp -s {staged} {installed} && ")


def _stub(bin_dir: Path, name: str, code: int, log: Path, say: str = "") -> None:
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / name
    said = f"echo '{say}' >&2\n" if say else ""
    stub.write_text(f'#!/bin/sh\necho "$@" >> {log}\n{said}exit {code}\n', encoding="utf-8")
    stub.chmod(0o755)


#: `launchctl print` 이 없는 라벨에 답하는 말(이 Mac 에서 잰 값, 종료 113)
NO_SERVICE = 'Could not find service "test.rcm.server" in domain for user gui: 501'


@pytest.mark.parametrize(("code", "say", "loaded"), [(0, "", True), (113, NO_SERVICE, False)])
def test_tr36_launchd_loaded_check_is_a_read_only_print(tmp_path, monkeypatch, code, say, loaded):
    # P5 A-E2 에서 113 만으로는 「없음」이 아니게 됐다 — 「Could not find service」 를 같이 답한다
    log = tmp_path / "calls.log"
    _stub(tmp_path / "bin", "launchctl", code, log, say)
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    assert setup.service_loaded("launchd", "test.rcm.server") is loaded
    assert log.read_text().splitlines() == [f"print gui/{os.getuid()}/test.rcm.server"]


def test_tr36_systemd_loaded_check_uses_only_is_active_and_is_enabled(tmp_path, monkeypatch):
    log = tmp_path / "calls.log"
    _stub(tmp_path / "bin", "systemctl", 0, log)
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    assert setup.service_loaded("systemd", "com.remote-ci-monitor.worker") is True
    words = {line.split()[1] for line in log.read_text().splitlines()}
    assert words <= {"is-active", "is-enabled"} and words
    assert all(line.split()[0] == "--user" for line in log.read_text().splitlines())
    assert all(line.split()[-1] == "rcm-worker.service" for line in log.read_text().splitlines())


def test_tr37_the_log_file_is_named_from_the_label(cfg, home, tmp_path):
    assert core.log_file_name("test.rcm.server") == "test.rcm.server.log"
    # 6-B-V7: 표준 라벨은 지금의 로그 이름 그대로다
    assert core.log_file_name(SERVER_LABEL) == "server.log"
    assert core.log_file_name(WORKER_LABEL) == "worker.log"
    assert go(Fake(), *coordinator_args(tmp_path / "data")) == 0
    doc = plistlib.loads((cfg / "service" / "test.rcm.server.plist").read_bytes())
    assert doc["StandardOutPath"] == str(home / "Library/Logs/rcm/test.rcm.server.log")


def test_tr37_one_systemd_unit_name_rule_for_setup():
    # 6-B-V1: 표준 라벨은 rcm-server · rcm-worker, 다른 라벨은 <label>.service
    assert core.unit_name(SERVER_LABEL) == "rcm-server.service"
    assert core.unit_name(WORKER_LABEL) == "rcm-worker.service"
    assert core.unit_name("test.rcm.server") == "test.rcm.server.service"
    assert core.service_file_name(SERVER_LABEL, "systemd") == "rcm-server.service"
    assert core.service_file_name(SERVER_LABEL, "launchd") == f"{SERVER_LABEL}.plist"


def test_tr24_doctor_finds_the_installed_copy_of_a_custom_label_service(
    cfg, home, tmp_path, monkeypatch
):
    """6-B-V1: setup 이 `--label` 로 쓴 서비스를 사람이 출력된 명령대로 설치하면 doctor 는 그 설치
    자리의 파일을 읽는다 — setup 자리의 사본이 아니라 서비스 관리자가 읽는 파일이다(6-B-V2)."""
    from remote_ci_monitor import doctor

    monkeypatch.setattr(doctor, "SYSTEM_UNIT_DIR", tmp_path / "system-units")
    monkeypatch.setattr(doctor, "LAUNCH_DAEMONS_DIR", tmp_path / "launch-daemons")
    data = tmp_path / "data"
    for platform, role, argv in (
        ("darwin", "coordinator-worker", coordinator_args(data)),
        ("linux", "coordinator-worker", coordinator_args(data, "--force")),
        (
            "darwin",
            "worker",
            ["--role", "worker", "--server", "http://10.0.0.5:18790", "--pool", "p1"]
            + ["--lanes", "1", "--label", "test.rcm.worker", "--yes", "--force"],
        ),
    ):
        fake = Fake(platform=platform)
        assert go(fake, *argv) == 0, fake.text
        manager = "launchd" if platform == "darwin" else "systemd"
        label = "test.rcm.worker" if role == "worker" else "test.rcm.server"
        name = core.service_file_name(label, manager)
        staged = cfg / "service" / name
        installed = core.install_dir(manager, home) / name
        assert f"cp -n {staged} {installed}" in fake.err.getvalue()
        # 설치하기 전: setup 자리의 사본을 읽는다
        found = doctor.pick_service_file(None, role, home=home, config_dir=cfg)
        assert found is not None and found.path == staged, (platform, role)
        installed.parent.mkdir(parents=True, exist_ok=True)
        installed.write_bytes(staged.read_bytes())
        found = doctor.pick_service_file(None, role, home=home, config_dir=cfg)
        assert found is not None and found.path == installed, (platform, role, found)
        installed.unlink()
        staged.unlink()  # 다음 경우의 setup 자리에 같은 역할의 파일이 둘 남지 않게
    # doctor 가 파일 이름에서 라벨을 되찾는 규칙은 setup 의 이름 규칙을 거꾸로 푼 것이다
    for label in (SERVER_LABEL, WORKER_LABEL, "test.rcm.server"):
        for manager in ("launchd", "systemd"):
            assert core.label_of_file(core.service_file_name(label, manager)) == (label, manager)
    assert core.label_of_file("notes.txt") is None


# ── P3 1라운드 ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "abbreviated",
    [["--conf", "x"], ["--data", "x"], ["--service", "x"], ["--lab", "x"], ["--for", "config"]]
    + [["--reuse"]],
)
def test_tr20_setup_takes_no_abbreviated_option(cfg, tmp_path, abbreviated):
    # P3 v1-1 · v2-2: 줄인 옵션은 guard 가 보는 이름과 다르다 — setup 이 받지 않는다(종료 2)
    from remote_ci_monitor import cli

    full = {"--conf": "--config", "--data": "--data-dir", "--service": "--service-file"}
    full |= {"--lab": "--label", "--for": "--force", "--reuse": "--reuse-data-dir"}
    whole = [full[abbreviated[0]], *abbreviated[1:]]
    # 대조: 온전한 이름은 받는다
    setup.build_parser().parse_args(["--role", "coordinator", *whole])
    with pytest.raises(SystemExit) as raised:
        setup.build_parser().parse_args(["--role", "coordinator", *abbreviated])
    assert raised.value.code == 2
    with pytest.raises(SystemExit) as raised:
        cli.build_parser().parse_args(["setup", "--role", "coordinator", *abbreviated])
    assert raised.value.code == 2


@pytest.mark.parametrize(
    ("platform", "where"),
    [
        ("darwin", "Library/LaunchAgents/test.rcm.server.plist"),
        ("linux", ".config/systemd/user/test.rcm.server.service"),
        ("linux", ".config/systemd/user/rcm-server.service.d/override.conf"),
        ("darwin", "/Library/LaunchDaemons/test.rcm.server.plist"),
        ("linux", "/etc/systemd/system/test.rcm.server.service"),
    ],
)
def test_tr24_setup_refuses_a_service_file_on_an_install_place(
    cfg, home, tmp_path, platform, where
):
    # P3 v1-2: 서비스 파일은 설치 자리에 쓰지 않는다(§4.1) — `--service-file` 로 가리켜도 같다
    target = Path(where) if where.startswith("/") else home / where
    fake = Fake(platform=platform)
    argv = coordinator_args(tmp_path / "data", "--service-file", str(target), "--force")
    assert go(fake, *argv) == 2, fake.text
    assert "install" in fake.err.getvalue()
    assert not target.exists()
    assert not (cfg / "server.toml").exists()


@pytest.mark.parametrize("odd", ["²", "١", "٣"])
def test_tr22_a_digit_that_is_not_ascii_is_asked_again(cfg, home, tmp_path, odd):
    # P3 v2-3: `"²".isdigit()` 은 참인데 `int("²")` 는 ValueError 다 — 다시 묻는다
    answers = [odd, "coordinator-worker", odd, "1", odd, "", "", "", ""]
    fake = Fake(answers=answers)
    assert go(fake) == 0, fake.text
    assert "Traceback" not in fake.text
    assert read_toml(cfg / "server.toml")["server"]["port"] == 8787
    fake = Fake(answers=["http://10.0.0.5:18790", "p1", odd, "2", ""])
    assert go(fake, "--role", "worker", "--force") == 0, fake.text
    assert read_toml(cfg / "worker.toml")["lanes"] == 2


def test_tr28_an_unexpected_error_is_one_line_and_exit_one(cfg, tmp_path):
    # P3 v2-3: 예상 못 한 예외는 traceback 이 아니라 한 줄 · 종료 1 이다
    def broken():
        raise RuntimeError("interfaces\nsecond line")

    fake = Fake(answers=["", "", "", ""], addresses=broken)
    assert go(fake, "--role", "coordinator-worker") == 1
    err = fake.err.getvalue()
    assert "RuntimeError" in err and "interfaces" in err
    assert "second line" not in err
    assert not (cfg / "server.toml").exists()


def test_tr28_a_data_dir_that_cannot_be_read_is_exit_one(cfg, tmp_path):
    # P3 v1-10: `rcm.sqlite3` 이 있는지 볼 수 없는 data_dir — PermissionError 는 종료 1 한 줄이다
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        fake = Fake()
        assert go(fake, *coordinator_args(locked / "data")) == 1
    finally:
        locked.chmod(0o755)
    assert str(locked / "data") in fake.err.getvalue()
    assert not (cfg / "server.toml").exists()


def test_tr28_a_path_that_is_not_utf8_is_a_usage_error(cfg, tmp_path):
    # P3 v2-10: UTF-8 이 아닌 argv(`os.fsdecode` 의 surrogate)는 설정 파일에 쓸 수 없다 — 종료 2
    odd = os.fsdecode(str(tmp_path).encode() + b"/d\xff")
    fake = Fake()
    assert go(fake, *coordinator_args(Path(odd))) == 2
    assert "UTF-8" in fake.err.getvalue()
    assert not (cfg / "server.toml").exists()


def test_tr28_ctrl_c_while_reporting_after_writing_is_exit_zero(cfg, tmp_path):
    # P3 v2-11 b: 파일을 쓴 뒤 보고 중의 Ctrl-C — traceback 없이 쓴 파일이 있다고 말한다.
    # P3 3라운드 v2-2: 그때는 파일을 다 썼으니 종료 0 이다(§4.1 「설정을 썼으면 0」, 이전 130)
    def interrupted():
        raise KeyboardInterrupt

    fake = Fake(host_facts=interrupted)
    try:
        code: int | str = go(fake, *coordinator_args(tmp_path / "data"))
    except KeyboardInterrupt:
        code = "KeyboardInterrupt escaped setup.run"  # 빨간 시험이 pytest 를 멈추지 않게
    assert code == 0
    err = fake.err.getvalue()
    assert "files written; report interrupted" in err and "nothing was written" not in err
    assert "Traceback" not in err
    assert (cfg / "server.toml").exists()


@pytest.mark.parametrize(
    "where",
    [
        "with space/home",
        "pct%h/home",
        "dollar$HOME/home",
        'quote"and\\back/home',
    ],
)
def test_tr11_a_systemd_unit_keeps_odd_paths_whole(tmp_path, where):
    # P3 v2-4 · v1-8: systemd 유닛의 경로는 공백 · `%` · `$` · 따옴표를 systemd 규칙대로 쓴다
    from remote_ci_monitor import cli, doctor

    home = tmp_path / where
    rcm = home / "venv dir" / "bin" / "rcm"
    config = home / ".config" / "rcm" / "server.toml"
    log = home / ".local" / "state" / "rcm" / "x.log"
    for text in (
        core.server_unit(
            rcm=rcm, config=config, home=home, label="t.s", log=log, path="/usr/bin:/bin"
        ),
        cli.worker_unit(rcm, config, home),
    ):
        unit = tmp_path / "x.service"
        unit.write_bytes(text)
        sf = doctor.read_service_file(unit, user_home=str(home))
        assert sf.argv[0] == str(rcm) and sf.argv[2:] == ["--config", str(config)], text.decode()
        assert sf.env.get("HOME", str(home)) == str(home)
        assert not sf.unfollowed, sf.unfollowed
        line = next(x for x in text.decode().splitlines() if x.startswith("WorkingDirectory="))
        assert line.removeprefix("WorkingDirectory=").replace("%%", "%") == str(home)


def test_tr11_a_line_break_in_a_unit_path_is_a_usage_error(cfg, home, tmp_path):
    # P3 v2-4: 줄바꿈 · 제어 문자는 유닛에 쓸 수 없다 — 쓰지 않고 종료 2
    fake = Fake(platform="linux")
    config = tmp_path / "odd\nname" / "server.toml"
    argv = coordinator_args(tmp_path / "data", "--config", str(config))
    assert go(fake, *argv) == 2, fake.text
    assert "systemd" in fake.err.getvalue()
    assert not config.exists()
    from remote_ci_monitor import cli

    with pytest.raises(ValueError):
        cli.worker_unit(RCM, config, home)


def test_tr10_the_interactive_run_never_offers_a_read_auth_mode(cfg, tmp_path):
    # P3 TR10 WEAK: 실제 대화형 stderr 에 없는 읽기 인증 모드(`read_auth`)가 나오지 않는다(Q14)
    for answer in ("1", "2"):
        fake = Fake(answers=[answer, "", "", "", ""])
        assert go(fake, "--role", "coordinator-worker", "--force") == 0, fake.text
        err = fake.err.getvalue()
        assert "anyone on this network can read" in err
        assert "read_auth" not in err and "auth =" not in err


def test_tr11_the_written_service_file_path_does_not_start_with_the_venv(cfg, home, tmp_path):
    # P3 TR11 WEAK: setup.run 이 실제로 쓴 파일 — rcm 이 venv bin 에 있어도 PATH 맨 앞은 아니다
    venv_rcm = tmp_path / "venv" / "bin" / "rcm"
    for platform in ("darwin", "linux"):
        fake = Fake(platform=platform, rcm=(venv_rcm, False))
        assert go(fake, *coordinator_args(tmp_path / "data", "--force")) == 0, fake.text
        if platform == "darwin":
            doc = plistlib.loads((cfg / "service" / "test.rcm.server.plist").read_bytes())
            path = doc["EnvironmentVariables"]["PATH"]
        else:
            text = (cfg / "service" / "test.rcm.server.service").read_text()
            line = next(x for x in text.splitlines() if x.startswith("Environment=PATH="))
            path = line.removeprefix("Environment=PATH=")
        entries = path.split(":")
        assert entries[0] != str(venv_rcm.parent), path
        assert str(venv_rcm.parent) not in entries
        assert "/usr/sbin" in entries


def test_tr22_an_unknown_answer_from_the_system_is_said(cfg, tmp_path):
    # P3 v1-5: 포트 · 서비스 조회가 모른다(None)면 그렇다고 한 줄 쓴다
    fake = Fake(port_in_use=lambda host, port: None, loaded=None)
    assert go(fake, *coordinator_args(tmp_path / "data")) == 0
    err = fake.err.getvalue()
    assert "could not tell whether port 18790" in err
    assert "could not tell whether a service labelled test.rcm.server" in err


def test_tr7_a_rollback_that_fails_is_named(cfg, tmp_path, monkeypatch):
    # P3 v1-6: 되돌리기가 실패하면 삼키지 않고 「could not restore <path>」라고 쓴다
    from remote_ci_monitor import cli

    cfg.mkdir(parents=True)
    (cfg / "server.toml").write_text("old = 1\n", encoding="utf-8")
    real = cli.write_private
    calls: list[Path] = []

    def flaky(path, data, mode):
        calls.append(path)
        if len(calls) >= 2:  # 서비스 파일에서, 그리고 되돌리기에서 실패한다
            raise OSError(13, "Permission denied")
        return real(path, data, mode)

    monkeypatch.setattr(cli, "write_private", flaky)
    fake = Fake()
    assert go(fake, *coordinator_args(tmp_path / "data", "--force")) == 1
    err = fake.err.getvalue()
    assert f"could not restore {cfg / 'server.toml'}" in err
    assert "nothing was written" not in err


@pytest.mark.parametrize(
    ("argv", "flags"),
    [
        (["--lanes", "3"], ["--lanes"]),
        (["--pool", "p", "--server", "http://10.0.0.5:1"], ["--pool", "--server"]),
    ],
)
def test_tr22_a_flag_the_role_does_not_use_is_a_warning(cfg, tmp_path, argv, flags):
    # P3 v1-7: 역할이 쓰지 않는 플래그는 조용히 버리지 않는다
    fake = Fake()
    assert go(fake, *coordinator_args(tmp_path / "data", *argv, role="coordinator")) == 0
    warned = [x for x in fake.err.getvalue().splitlines() if "not used by --role" in x]
    assert len(warned) == 1, fake.err.getvalue()
    assert all(flag in warned[0] for flag in flags)
    fake = Fake()
    argv = ["--role", "client", "--server", "http://10.0.0.5:1", "--advertise", "--yes"]
    assert go(fake, *argv) == 0
    err = fake.err.getvalue()
    assert "--advertise" in err and "not used by --role client" in err


def test_tr36_the_start_line_says_when_it_did_not_start(tmp_path):
    # P3 v1-9: 설치 자리의 파일이 다르면 조용히 끝나지 않고 그렇다고 말한다
    staged, installed = tmp_path / "s.plist", tmp_path / "LaunchAgents" / "s.plist"
    for manager in ("launchd", "systemd"):
        lines = core.install_commands(
            manager, staged=staged, installed=installed, label="s", log=tmp_path / "s.log"
        )
        start = lines[-1]
        assert start.startswith(f"cmp -s {staged} {installed} && ")
        assert '|| echo "' in start and "not started" in start


def test_tr28_the_no_manager_warning_quotes_the_config_path(cfg, tmp_path):
    # P3 v2-12: 경고 속 명령도 붙여 넣을 수 있게 따옴표를 친다
    config = tmp_path / "with space" / "server.toml"
    fake = Fake(platform="freebsd")
    assert go(fake, *coordinator_args(tmp_path / "data", "--config", str(config))) == 0
    assert f"rcm serve --config '{config}'" in fake.err.getvalue()


@pytest.mark.parametrize("url", ["http://", "https://", "http:///x", "http://:8787"])
def test_tr26_a_url_without_a_host_is_a_usage_error(cfg, tmp_path, url):
    # P3 v2-12: 호스트 없는 URL 은 설정에 쓰지 않는다
    fake = Fake()
    assert go(fake, "--role", "client", "--server", url, "--yes") == 2
    assert "--server" in fake.err.getvalue()
    assert not (cfg / "client.toml").exists()


# ── P3 2라운드: guard 와 setup 이 같은 해석기를 쓴다 ─────────────────────────


def test_tr20_an_empty_path_or_label_is_a_usage_error(cfg, tmp_path):
    # P3 2라운드 v2-1: 빈 `--config` 는 기본 자리(운영 설정 폴더일 수 있다)로 떨어졌다 — 빈 값 ·
    # 공백뿐인 값은 사용법 오류(종료 2)이고 아무것도 쓰지 않는다
    for extra in (
        ["--config="],
        ["--config", ""],
        ["--config", "   "],
        ["--service-file="],
        ["--service-file", " "],
        ["--data-dir", ""],
        ["--data-dir=  "],
        ["--label", ""],
        ["--label", " "],
    ):
        fake = Fake()
        code = go(fake, *coordinator_args(tmp_path / "data"), *extra)
        assert code == 2, (extra, fake.text)
        assert "empty" in fake.err.getvalue(), (extra, fake.text)
        assert not cfg.exists(), extra
        assert not (tmp_path / "data").exists(), extra


def test_tr20_setup_writes_exactly_where_the_shared_resolver_says(home, tmp_path, monkeypatch):
    # P3 2라운드 근본 원인: guard 는 setup 이 어디에 쓸지를 setup 과 따로 계산했다. 이제 둘 다
    # `core.where` 하나를 쓴다 — setup 이 실제로 쓴 자리가 그 함수의 답과 같다(상대 경로 · `~` ·
    # 상대 XDG · `--config` · `--service-file` · `--data-dir` 의 조합 전부)
    import itertools

    where = getattr(core, "where", None)
    assert where is not None, "core.where — the one resolver guard and setup share"
    monkeypatch.setenv("XDG_CONFIG_HOME", "rel-xdg")
    roles = ("worker", "coordinator-worker", "client")
    configs = (None, "c/s.toml", "~/c{n}/s.toml")
    services = (None, "svc/f.x", "~/svc{n}/f.x")
    datas = ("d", "~/d{n}")
    for n, (role, config, service, data) in enumerate(
        itertools.product(roles, configs, services, datas)
    ):
        work = tmp_path / f"w{n}"
        work.mkdir()
        monkeypatch.chdir(work)
        given = {
            "config": config and config.format(n=n),
            "service_file": service and service.format(n=n),
            "data_dir": data.format(n=n),
        }
        if role == "client":
            argv = ["--role", role, "--server", "http://10.0.0.5:18790", "--yes"]
            label = None
        elif role == "worker":
            argv = ["--role", role, "--server", "http://10.0.0.5:18790", "--pool", "p"]
            argv += ["--lanes", "1", "--yes"]
            label = f"test.rcm.w{n}"
        else:
            argv = ["--role", role, "--bind", "127.0.0.1", "--port", "18790", "--no-advertise"]
            argv += ["--yes"]
            label = f"test.rcm.s{n}"
        if label:
            argv += ["--label", label]
        for flag, value in given.items():
            if value is not None:
                argv += [f"--{flag.replace('_', '-')}", value]
        fake = Fake()
        assert go(fake, *argv) == 0, (argv, fake.text)
        want = where(
            role,
            config=given["config"],
            label=label,
            service_file=given["service_file"],
            data_dir=given["data_dir"],
            environ=dict(os.environ),
            cwd=Path.cwd(),
            home=Path.home(),
            manager=core.LAUNCHD,
            fold_case=core.folds_case("darwin"),
            expand_user=os.path.expanduser,
        )
        wrote = {Path(line) for line in fake.out.getvalue().splitlines()}
        assert wrote == {p for p in (want.config, want.service_file) if p is not None}, argv
        assert all(p.is_file() for p in wrote), argv
        if role == "coordinator-worker":
            assert read_toml(want.config)["server"]["data_dir"] == str(want.data_dir), argv


def test_tr24_an_install_place_in_another_case_is_refused(cfg, home, tmp_path, monkeypatch):
    # P3 2라운드: macOS 의 기본 파일시스템은 대소문자를 가리지 않는다 — `~/library/launchagents`
    # 도 서비스 관리자가 읽는 자리다. 접는 규칙은 setup 이 도는 플랫폼(`World.platform`)에서 온다
    target = home / "library" / "launchagents" / "test.rcm.server.plist"
    fake = Fake(platform="darwin")
    argv = coordinator_args(tmp_path / "data", "--service-file", str(target))
    assert go(fake, *argv) == 2, fake.text
    assert "install" in fake.err.getvalue()
    assert not target.exists()


def test_tr20_a_label_with_a_trailing_line_break_is_a_usage_error(cfg, tmp_path):
    # P3 2라운드 v1-5: 라벨 정규식은 끝까지 맞아야 한다 — `$` 는 끝 줄바꿈 앞에서도 맞는다
    assert core.label_problem("test.rcm.server\n") is not None
    fake = Fake()
    argv = [*coordinator_args(tmp_path / "data"), "--label", "test.rcm.server\n"]
    assert go(fake, *argv) == 2, fake.text
    assert not cfg.exists()


def test_tr28_a_report_that_fails_after_writing_is_exit_zero(cfg, tmp_path):
    # P3 2라운드 v2-1(MED): 설정을 다 쓴 뒤 보고가 터지면 종료 0 이다(§4.1 「설정을 썼으면 0」)
    def broken():
        raise RuntimeError("facts\nsecond line")

    fake = Fake(host_facts=broken)
    assert go(fake, *coordinator_args(tmp_path / "data")) == 0, fake.text
    err = fake.err.getvalue()
    assert "files written; the report failed: RuntimeError: facts" in err
    assert "second line" not in err and "Traceback" not in err
    assert (cfg / "server.toml").is_file()


def test_tr20_the_hook_reads_setup_with_the_same_parser_and_default(cfg):
    # P3 2라운드: 훅이 쓰는 파서와 기본 data_dir 이 setup 의 것과 같다 — 사본이 아니라 같은 정의다
    from remote_ci_monitor import cli

    template = cli.read_template("server.toml").decode("utf-8")
    assert core.template_value(template, "data_dir") == core.DEFAULT_DATA_DIR
    argv = ["--role", "worker", "--config", "x.toml", "--data-dir=d", "--force", "--yes"]
    assert vars(core.parse_args(argv)) == vars(setup.build_parser().parse_args(argv))
    for bad in (["--conf", "x"], ["--role", "bogus"], ["--port", "x"], ["--nope"]):
        with pytest.raises(core.SetupArgError):
            core.parse_args(bad)


def test_tr11_execstart_writes_a_dollar_twice():
    # P3 2라운드 v2-2: ExecStart 의 `$` 는 systemd 가 환경변수로 펼친다 — `$$` 로 써야 글자다.
    # 도구가 읽는 쪽(doctor)이 `$$` 를 `$` 로 돌리는 것은 test_tr11_a_systemd_unit_keeps_… 가 본다
    assert core.exec_start(["/x/a$HOME/rcm", "serve"]) == "/x/a$$HOME/rcm serve"
    assert core.unit_word("PATH=/a$b") == "PATH=/a$b"  # Environment= 에서는 그대로다


# ── P3 3라운드 ──


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_tr24_setup_refuses_a_config_inside_an_install_place(cfg, home, tmp_path, platform):
    # P3 3라운드 v1-1: 설정 파일도 서비스 파일과 같은 규칙이다 — 서비스 관리자가 읽는 자리(사용자
    # LaunchAgents · systemd 사용자 유닛 폴더) 안의 `--config` 는 사용법 오류다
    for place in ("Library/LaunchAgents", ".config/systemd/user"):
        target = home / place / "com.remote-ci-monitor.server.plist"
        fake = Fake(platform=platform)
        service = tmp_path / "svc" / "test.rcm.server.plist"
        argv = coordinator_args(tmp_path / "data", "--config", str(target))
        argv += ["--service-file", str(service), "--force", "all"]
        assert go(fake, *argv) == 2, (place, fake.text)
        assert "install" in fake.err.getvalue(), place
        assert not target.exists() and not service.exists(), place


def test_tr28_ctrl_c_before_anything_is_written_is_130(cfg, tmp_path):
    # 쓰기 전의 Ctrl-C 는 130 이고 아무것도 쓰지 않았다고 말한다(§4.1)
    def interrupted():
        raise KeyboardInterrupt

    fake = Fake(find_rcm=interrupted)
    assert go(fake, *coordinator_args(tmp_path / "data")) == 130
    assert "nothing was written" in fake.err.getvalue()
    assert not cfg.exists()


def test_tr20_the_resolver_reads_nothing_from_the_machine():
    # P3 3라운드 v1-5: `core/setup.py` 는 I/O 가 없다 — `~user` 의 passwd 조회(`expanduser`)와
    # `sys.platform` 은 호출자가 준다
    import ast

    tree = ast.parse(Path(core.__file__).read_text(encoding="utf-8"))
    reads = sorted(
        {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and node.attr in ("expanduser", "platform", "environ", "home", "getcwd", "cwd")
        }
    )
    assert reads == []


def test_tr20_where_takes_the_user_lookup_and_the_case_rule_from_its_caller(tmp_path):
    # P3 3라운드 v1-5: 해석기는 `~user` 를 푸는 함수와 대소문자 규칙을 인자로 받는다
    import inspect

    assert {"fold_case", "expand_user"} <= set(inspect.signature(core.where).parameters)
    seen: list[str] = []

    def users(raw: str) -> str:
        seen.append(raw)
        return "/users/bob" + raw[len("~bob") :]

    folded = tmp_path / "library" / "launchagents" / "x.plist"
    for fold, placed in ((True, True), (False, False)):
        found = core.where(
            "worker",
            environ={},
            cwd=tmp_path,
            home=tmp_path,
            manager=core.LAUNCHD,
            config="~bob/w.toml",
            label="test.rcm.worker",
            service_file=str(folded),
            fold_case=fold,
            expand_user=users,
        )
        assert found.config == Path("/users/bob/w.toml")
        assert (found.install_place is not None) is placed, fold
    assert seen == ["~bob/w.toml", "~bob/w.toml"]


# ── P5 GAP (batch 7): 출력 순서 · 제거 · 서비스 관리자의 답 · 설정 스캔 · --bind ──

WORKER_ARGV = [
    "--role",
    "worker",
    "--server",
    "http://10.0.0.5:18790",
    "--pool",
    "p1",
    "--lanes",
    "1",
    "--label",
    "test.rcm.worker",
    "--yes",
]


def _lines(fake: Fake) -> list[str]:
    return [line.strip() for line in fake.err.getvalue().splitlines()]


def _at(lines: list[str], text: str) -> int:
    found = [i for i, line in enumerate(lines) if line == text]
    assert found, (text, lines)
    return found[0]


def test_s1_a_loaded_label_prints_the_launchd_swap_order(cfg, home, tmp_path):
    # B-E2: 이미 올라간 라벨이면 bootout → cp(-n 없이) → bootstrap 순서를 찍는다
    fake = Fake(loaded=True)
    assert go(fake, *coordinator_args(tmp_path / "data")) == 0
    staged = cfg / "service" / "test.rcm.server.plist"
    installed = home / "Library" / "LaunchAgents" / "test.rcm.server.plist"
    lines = _lines(fake)
    out = _at(lines, "launchctl bootout gui/$(id -u)/test.rcm.server")
    copy = _at(lines, f"cp {staged} {installed}")
    start = _at(lines, f"launchctl bootstrap gui/$(id -u) {installed}")
    assert out < copy < start
    # 「not started」 줄도 그 길을 가리킨다
    said = next(line for line in lines if "not started" in line)
    assert "bootout" in said and "cp without -n" in said


def test_s1_force_service_prints_the_systemd_swap_order(cfg, home, tmp_path):
    argv = coordinator_args(tmp_path / "data")
    assert go(Fake(platform="linux"), *argv) == 0
    fake = Fake(platform="linux")
    assert go(fake, *argv, "--force") == 0, fake.text
    unit = "test.rcm.server.service"
    staged = cfg / "service" / unit
    installed = home / ".config" / "systemd" / "user" / unit
    lines = _lines(fake)
    off = _at(lines, f"systemctl --user disable --now {unit}")
    copy = _at(lines, f"cp {staged} {installed}")
    reload = _at(lines, "systemctl --user daemon-reload")
    on = _at(lines, f"systemctl --user enable --now {unit}")
    assert off < copy < reload < on


def test_s1_a_first_setup_prints_no_swap(cfg, tmp_path):
    fake = Fake()
    assert go(fake, *coordinator_args(tmp_path / "data")) == 0
    assert not any(line.startswith("launchctl bootout") for line in _lines(fake))


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_s2_the_output_says_how_to_remove_it(cfg, home, tmp_path, platform):
    # B-E3: 출력 끝(마지막 doctor 줄 바로 앞)에 제거하는 한 줄
    fake = Fake(platform=platform)
    assert go(fake, *coordinator_args(tmp_path / "data")) == 0
    lines = _lines(fake)
    remove = lines[-2]
    assert remove.startswith("to remove: "), lines[-3:]
    if platform == "darwin":
        installed = home / "Library" / "LaunchAgents" / "test.rcm.server.plist"
        assert "launchctl bootout gui/$(id -u)/test.rcm.server" in remove
    else:
        installed = home / ".config" / "systemd" / "user" / "test.rcm.server.service"
        assert "systemctl --user disable --now test.rcm.server.service" in remove
    assert f"rm -f {installed}" in remove and str(cfg / "server.toml") in remove


def test_s2_a_client_is_told_which_file_to_remove(cfg, tmp_path):
    fake = Fake()
    assert go(fake, "--role", "client", "--server", "http://10.0.0.5:1", "--yes") == 0
    assert _lines(fake)[-2] == f"to remove: rm -f {cfg / 'client.toml'}"


def test_s3_on_linux_linger_comes_before_the_start(cfg, tmp_path):
    # B-E5: 헤드리스 머신에는 user manager 가 없다 — linger 가 시작 명령보다 앞이다
    fake = Fake(platform="linux")
    assert go(fake, *coordinator_args(tmp_path / "data")) == 0
    lines = _lines(fake)
    linger = next(i for i, line in enumerate(lines) if "enable-linger" in line)
    start = next(i for i, line in enumerate(lines) if "enable --now" in line)
    assert linger < start
    assert "user manager" in lines[linger] and lines[linger].startswith("first")


def test_s4_the_last_doctor_line_names_the_rcm_and_the_paths_given(cfg, tmp_path):
    # B-E7: setup 이 쓴 rcm 의 절대 경로, 사용자가 준 --config · --service-file
    config, sf = tmp_path / "w" / "server.toml", tmp_path / "w" / "test.rcm.server.plist"
    fake = Fake()
    argv = coordinator_args(tmp_path / "data", "--config", str(config), "--service-file", str(sf))
    assert go(fake, *argv) == 0
    last = _lines(fake)[-1]
    want = f"{RCM} doctor --role coordinator-worker --config {config} --service-file {sf}"
    assert want in last, last
    fake = Fake()
    assert go(fake, *coordinator_args(tmp_path / "data2")) == 0
    last = _lines(fake)[-1]
    assert f"`{RCM} doctor --role coordinator-worker`" in last and "--config" not in last


def test_s5_a_worker_puts_the_token_in_before_starting_the_service(cfg, tmp_path):
    # B-E9: 워커는 토큰을 넣고 → 서비스를 시작한다
    fake = Fake()
    assert go(fake, *WORKER_ARGV) == 0
    err = fake.err.getvalue()
    assert err.index("token:") < err.index("next — install and start")
    last = _lines(fake)[-1]
    assert last.index("token") < last.index("start the service"), last


def test_s6_the_systemd_worker_unit_logs_to_its_label_and_sets_home(cfg, home, tmp_path):
    # B-E13 · A-E9: 서버 유닛과 같은 방식 — 라벨 로그 · HOME · setup 이 썼다는 머리 주석
    assert go(Fake(platform="linux"), *WORKER_ARGV) == 0
    text = (cfg / "service" / "test.rcm.worker.service").read_text(encoding="utf-8")
    log = home / ".local" / "state" / "rcm" / "test.rcm.worker.log"
    assert f"StandardOutput=append:{log}\n" in text
    assert f"StandardError=append:{log}\n" in text
    assert f"Environment=HOME={home}\n" in text
    assert "written by `rcm setup`" in text.splitlines()[0]
    # `rcm init worker --service-file` 이 쓰는 유닛은 그대로다
    from remote_ci_monitor import cli

    init = cli.worker_unit(RCM, cfg / "worker.toml", home).decode("utf-8")
    assert "written by `rcm init worker --service-file`" in init.splitlines()[0]


def _manager_stub(bin_dir: Path, name: str, script: str) -> None:
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / name
    stub.write_text("#!/bin/sh\n" + script, encoding="utf-8")
    stub.chmod(0o755)


@pytest.mark.parametrize(
    ("script", "loaded"),
    [
        ("exit 0\n", True),
        (
            'echo "Could not find service \\"x\\" in domain for user gui: 501" >&2\nexit 113\n',
            False,
        ),
        ('echo "Could not find domain for user gui: 99999" >&2\nexit 112\n', None),
        ('echo "Could not print domain: 1: Operation not permitted" >&2\nexit 1\n', None),
        ("exit 113\n", None),
    ],
)
def test_s7_launchd_only_a_missing_service_is_not_loaded(tmp_path, monkeypatch, script, loaded):
    # A-E2: launchctl print 의 「Could not find service」 만 「없음」이다. 도메인이 없거나 다른
    # 실패는 「모름」이다. 이 Mac 에서 잰 값: 없는 라벨은 113 과 Could not find service, 없는
    # 도메인은 112 와 Could not find domain
    _manager_stub(tmp_path / "bin", "launchctl", script)
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    assert setup.service_loaded("launchd", "test.rcm.server") is loaded


@pytest.mark.parametrize(
    ("active", "enabled", "loaded"),
    [(0, 1, True), (3, 0, True), (3, 1, False), (1, 1, None), (4, 1, None)],
)
def test_s7_systemd_only_an_inactive_unit_is_not_loaded(
    tmp_path, monkeypatch, active, enabled, loaded
):
    # systemctl is-active 의 3 은 「active 아님」(systemctl(1) Exit status), 1 은 「Failed to
    # connect to bus」 같은 실패다 — 모름
    # 진짜 systemctl 처럼 is-active 는 상태 낱말을 stdout 에 찍는다(batch 8 부터 3 은 그 낱말과
    # 함께 읽는다)
    word = "active" if active == 0 else "inactive"
    script = f'case "$2" in is-active) echo {word}; exit {active};; *) exit {enabled};; esac\n'
    _manager_stub(tmp_path / "bin", "systemctl", script)
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    assert setup.service_loaded("systemd", "test.rcm.server") is loaded


def test_s7_an_unknown_answer_warns_that_the_start_may_fail(cfg, tmp_path):
    fake = Fake(loaded=None)
    assert go(fake, *coordinator_args(tmp_path / "data")) == 0
    err = fake.err.getvalue()
    assert "could not tell" in err and "start command" in err and "may fail" in err


def test_s8_tokens_are_printed_without_a_service_manager(cfg, tmp_path):
    # A-E3: 서비스 관리자가 없는 OS 에서도 토큰 명령(TR21 · TR27)
    fake = Fake(platform="freebsd")
    assert go(fake, *coordinator_args(tmp_path / "data")) == 0
    config = cfg / "server.toml"
    err = fake.err.getvalue()
    assert f"{RCM} token --config {config} add CLIENT_NAME" in err
    assert f"{RCM} token --config {config} add WORKER_NAME --worker" in err


@pytest.mark.parametrize("raw", [b"[server\nport = 1\n", b"\xff\xfe[server]\n"])
def test_s9_an_unreadable_existing_config_is_a_warning(cfg, tmp_path, raw):
    # A-E5: 역할 스캔이 setup 을 막지 않는다. 그 파일을 덮는 것은 --force config 일 때만
    cfg.mkdir(parents=True)
    (cfg / "server.toml").write_bytes(raw)
    fake = Fake()
    assert go(fake, *WORKER_ARGV) == 0, fake.text
    assert f"could not read {cfg / 'server.toml'}" in fake.err.getvalue()
    assert (cfg / "server.toml").read_bytes() == raw
    assert go(Fake(), *coordinator_args(tmp_path / "data")) == 1
    assert (cfg / "server.toml").read_bytes() == raw
    fake = Fake()
    assert go(fake, *coordinator_args(tmp_path / "data"), "--force", "config") == 0, fake.text
    assert read_toml(cfg / "server.toml")["server"]["port"] == 18790


@pytest.mark.parametrize("name", ["localhost", "macmini.local", "my-host"])
def test_s10_bind_takes_an_address_not_a_name(cfg, tmp_path, name):
    # A-E7: 이름은 거절하고 풀지 않는다(DNS · mDNS 를 묻지 않는다)
    asked: list[str] = []

    def local(address):
        asked.append(address)
        return True

    fake = Fake(is_local_address=local)
    argv = coordinator_args(tmp_path / "data")
    argv[argv.index("--bind") + 1] = name
    assert go(fake, *argv) == 2
    assert "give an address, not a name" in fake.err.getvalue()
    assert asked == []
    assert not (cfg / "server.toml").exists()


def test_s10_a_typed_name_is_asked_again(cfg, tmp_path):
    answers = ["coordinator-worker", "localhost", "127.0.0.1", "", "", "n", ""]
    fake = Fake(answers=answers)
    assert go(fake, "--data-dir", str(tmp_path / "data"), "--label", "test.rcm.server") == 0
    assert "give an address, not a name" in fake.err.getvalue()
    assert read_toml(cfg / "server.toml")["server"]["bind"] == "127.0.0.1"


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_s11_korean_paths_round_trip(tmp_path, monkeypatch, platform):
    # A-E8: 한글 HOME · config · data_dir · 서비스 파일 · rcm 경로를 쓰고 되읽는다
    from remote_ci_monitor import doctor
    from remote_ci_monitor.config import load_server_config

    h = tmp_path / "집 폴더"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "설정"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "자료"))
    for name in ("RCM_CONFIG", "RCM_SERVER", "RCM_TOKEN", "RCM_WORKER_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("RCM_SERVER_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    rcm = tmp_path / "가상 환경" / "bin" / "rcm"
    config = tmp_path / "설정" / "서버" / "server.toml"
    data = tmp_path / "데이터" / "rcm"
    suffix = "plist" if platform == "darwin" else "service"
    sf = tmp_path / "서비스" / f"test.rcm.server.{suffix}"
    fake = Fake(platform=platform, rcm=(rcm, False))
    argv = coordinator_args(data, "--config", str(config), "--service-file", str(sf))
    assert go(fake, *argv) == 0, fake.text
    assert load_server_config(config, environ={}, check_tools=False).data_dir == data
    read = doctor.read_service_file(sf, user_home=str(h) if platform == "linux" else None)
    assert read.program == str(rcm) and read.env.get("HOME") == str(h)
    assert read.launch is not None and read.launch.get("config") == str(config), read.launch
    if platform == "darwin":
        doc = plistlib.loads(sf.read_bytes())
        assert doc["ProgramArguments"] == [str(rcm), "serve", "--config", str(config)]
        assert doc["StandardOutPath"] == str(h / "Library" / "Logs" / "rcm" / "test.rcm.server.log")


# ── P6 재확인(batch 8) 3 · 4 · 5 · 6 ──


def _installed_and_staged(cfg: Path, home: Path, platform: str) -> tuple[Path, Path]:
    if platform == "darwin":
        name = "test.rcm.server.plist"
        return cfg / "service" / name, home / "Library" / "LaunchAgents" / name
    name = "test.rcm.server.service"
    return cfg / "service" / name, home / ".config" / "systemd" / "user" / name


@pytest.mark.parametrize("same", [True, False])
@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_b8_3_the_remove_line_takes_the_installed_file_only_when_setup_wrote_it(
    cfg, home, tmp_path, platform, same
):
    # v2-3: 설치 자리의 파일이 setup 이 쓴 파일과 같을 때만(cmp) 서비스를 내리고 지운다. 다른
    # 파일이면 그대로 두고 setup 이 쓴 파일만 지운다. 서비스 관리자는 PATH 의 가짜다
    fake = Fake(platform=platform)
    assert go(fake, *coordinator_args(tmp_path / "data")) == 0
    remove = _lines(fake)[-2]
    assert remove.startswith("to remove: "), remove
    staged, installed = _installed_and_staged(cfg, home, platform)
    installed.parent.mkdir(parents=True, exist_ok=True)
    installed.write_bytes(staged.read_bytes() if same else b"a service someone else wrote\n")
    bin_dir, log = tmp_path / "stub-bin", tmp_path / "manager.log"
    for manager in ("launchctl", "systemctl"):
        _manager_stub(bin_dir, manager, f'echo "$*" >> {log}\nexit 0\n')
    for tool in ("cmp", "rm", "id"):
        found = shutil.which(tool)
        assert found is not None, tool
        (bin_dir / tool).symlink_to(found)
    env = {"PATH": str(bin_dir), "HOME": str(home)}
    subprocess.run(["/bin/sh", "-c", remove.removeprefix("to remove: ")], env=env, check=False)
    assert installed.exists() is not same, remove
    assert not staged.exists() and not (cfg / "server.toml").exists()
    called = log.read_text() if log.exists() else ""
    assert ("bootout" in called or "disable" in called) is same, called


def test_b8_3_a_label_already_loaded_may_not_be_setups(cfg, tmp_path):
    # setup 이 「건드리지 않는다」고 한 이미 올라간 라벨이면 제거 줄이 그 서비스는 setup 이 쓴
    # 것이 아닐 수 있다고 말한다
    fake = Fake(loaded=True)
    assert go(fake, *coordinator_args(tmp_path / "data")) == 0
    remove = _lines(fake)[-2]
    assert remove.startswith("to remove: ") and "may not be one setup wrote" in remove, remove
    fake = Fake()
    assert go(fake, *coordinator_args(tmp_path / "data2"), "--force", "all") == 0
    assert "may not be one setup wrote" not in _lines(fake)[-2]


@pytest.mark.parametrize(
    ("state", "loaded"),
    [
        ("inactive", False),
        ("unknown", False),
        ("activating", None),
        ("reloading", None),
        ("", None),
    ],
)
def test_b8_4_systemd_exit_3_is_absent_only_for_an_absent_state(
    tmp_path, monkeypatch, state, loaded
):
    # v1-2 · v2: is-active 가 3 이어도 stdout 이 activating · reloading 이면 모른다. inactive ·
    # unknown 만 「없음」이다(failed 는 batch 9 v1-7 에서 「있음」으로 옮겼다)
    script = f'case "$2" in is-active) echo "{state}"; exit 3;; *) echo "disabled"; exit 1;; esac\n'
    _manager_stub(tmp_path / "bin", "systemctl", script)
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    assert setup.service_loaded("systemd", "test.rcm.server") is loaded


@pytest.mark.parametrize("how", ["file", "RCM_SERVER", "RCM_TOKEN"])
def test_b8_5_an_unreadable_server_config_still_finds_the_client_role(cfg, monkeypatch, how):
    # v1-3: 읽지 못하는 server.toml 의 길에서도 클라이언트 역할(client.toml · 환경)을 찾는다 —
    # doctor.infer_roles 와 같은 규칙
    cfg.mkdir(parents=True)
    (cfg / "server.toml").write_bytes(b"[server\nport = 1\n")
    if how == "file":
        (cfg / "client.toml").write_text('server = "http://10.0.0.5:1"\n')
    else:
        monkeypatch.setenv(how, "x")
    roles, warning = setup.scan_roles(os.environ)
    assert warning is not None and "could not read" in warning
    assert roles == ["client"], roles


def test_b8_6_a_bracketed_bind_is_told_to_drop_the_brackets(cfg, tmp_path):
    # LOW: `[::1]` 은 이름이 아니라 괄호를 친 주소다 — 그렇게 말한다. 괄호 친 이름은 이름이다
    fake = Fake(local=("::1",))
    argv = coordinator_args(tmp_path / "data")
    argv[argv.index("--bind") + 1] = "[::1]"
    assert go(fake, *argv) == 2
    err = fake.err.getvalue()
    assert "without the brackets" in err and "not a name" not in err, err
    fake = Fake()
    argv[argv.index("--bind") + 1] = "[my-host]"
    assert go(fake, *argv) == 2
    assert "give an address, not a name" in fake.err.getvalue()
    assert not (cfg / "server.toml").exists()


@pytest.mark.parametrize("spelled", ["0:0:0:0:0:0:0:0", "::0", "0::"])
def test_b8_6_an_all_zero_ipv6_bind_is_written_as_the_all_address(cfg, tmp_path, spelled):
    # 모두 0 인 IPv6 꼴은 `::` 로 적는다 — 그래야 「모든 주소」 경고와 같은 규칙으로 읽힌다
    seen: list[str] = []

    def local(address):
        seen.append(address)
        return address == "::"

    fake = Fake(is_local_address=local)
    argv = coordinator_args(tmp_path / "data")
    argv[argv.index("--bind") + 1] = spelled
    assert go(fake, *argv) == 0, fake.text
    assert read_toml(cfg / "server.toml")["server"]["bind"] == "::"
    assert seen == ["::"]


def test_b8_6_a_typed_bracketed_address_is_asked_again(cfg, tmp_path):
    answers = ["coordinator-worker", "[::1]", "127.0.0.1", "", "", "n", ""]
    fake = Fake(answers=answers)
    assert go(fake, "--data-dir", str(tmp_path / "data"), "--label", "test.rcm.server") == 0
    assert "without the brackets" in fake.err.getvalue()
    assert read_toml(cfg / "server.toml")["server"]["bind"] == "127.0.0.1"


# ── P6 재확인(batch 9) v1-5 · v2-4 · v1-7 ──


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_b9_the_remove_line_removes_setups_file_even_when_the_unload_fails(
    cfg, home, tmp_path, platform
):
    # v1-5 · v2-4: 서비스 관리자가 내리기에 실패해도(이미 내려간 서비스 · 닿지 않는 버스) cmp 가
    # 같으면 설치 자리의 파일은 지운다 — `cmp && { 내리기; rm; }` 꼴이다
    fake = Fake(platform=platform)
    assert go(fake, *coordinator_args(tmp_path / "data")) == 0
    remove = _lines(fake)[-2]
    staged, installed = _installed_and_staged(cfg, home, platform)
    installed.parent.mkdir(parents=True, exist_ok=True)
    installed.write_bytes(staged.read_bytes())
    bin_dir, log = tmp_path / "stub-bin", tmp_path / "manager.log"
    for manager in ("launchctl", "systemctl"):
        _manager_stub(bin_dir, manager, f'echo "$*" >> {log}\necho "unload failed" >&2\nexit 5\n')
    for tool in ("cmp", "rm", "id"):
        found = shutil.which(tool)
        assert found is not None, tool
        (bin_dir / tool).symlink_to(found)
    env = {"PATH": str(bin_dir), "HOME": str(home)}
    subprocess.run(["/bin/sh", "-c", remove.removeprefix("to remove: ")], env=env, check=False)
    assert not installed.exists(), remove
    assert not staged.exists() and not (cfg / "server.toml").exists()
    called = log.read_text()
    assert "bootout" in called or "disable" in called, called


def test_b9_systemd_failed_means_the_unit_is_there(tmp_path, monkeypatch):
    # v1-7: is-active 의 `failed` 는 유닛이 있다는 뜻이다(없는 유닛은 inactive) — 「없음」이 아니다
    script = 'case "$2" in is-active) echo failed; exit 3;; *) echo disabled; exit 1;; esac\n'
    _manager_stub(tmp_path / "bin", "systemctl", script)
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    assert setup.service_loaded("systemd", "test.rcm.server") is True


# ── P6 재확인(batch 9) 352: setup 자신이 쓰기 직전에 운영을 알아본다 ──
#
# 「운영 서비스」는 모두 임시 HOME 아래의 가짜 plist · 유닛이다. 진짜 서비스 관리자는 부르지 않고
# 시스템 자리(`setup.SYSTEM_SERVICE_PLACES`)도 임시 폴더로 바꿔 끼운다.


#: 진짜 시스템 자리 — 시험 하나만 읽는다(읽기만, batch 10 M6)
REAL_SYSTEM_SERVICE_PLACES = setup.SYSTEM_SERVICE_PLACES


@pytest.fixture(autouse=True)
def no_system_places(tmp_path, monkeypatch):
    """모든 시험에서 setup 의 스캔이 진짜 시스템 자리(`/Library/Launch*` · `/etc/systemd`)와 진짜
    계정 홈(passwd)을 읽지 않게 임시 폴더로 바꿔 끼운다(batch 10 M6) — CI 맥미니에는 rcm 서비스가
    있다. 환경 변수로 바꾸는 길은 setup 에 없다(운영 우회가 된다)."""
    places = tuple(tmp_path / "sys" / name for name in ("LaunchAgents", "LaunchDaemons", "system"))
    monkeypatch.setattr(setup, "SYSTEM_SERVICE_PLACES", places)
    monkeypatch.setattr(setup, "account_home", lambda: tmp_path / "account-home")
    return places


@pytest.fixture
def prod(home, tmp_path, no_system_places):
    """임시 HOME 의 가짜 운영 서버: LaunchAgents 의 표준 라벨 plist 가 venv 의 rcm 을
    `serve --config ~/.config/rcm/server.toml` 로 띄우고, 그 venv 는 체크아웃을 editable 로
    설치했고, 설정의 data_dir 은 따로 있다."""
    return _production(home, tmp_path)


def _production(home: Path, tmp_path: Path) -> dict[str, Path]:
    checkout = tmp_path / "svc" / "remote_ci_monitor-service"
    (checkout / "src").mkdir(parents=True)
    venv = tmp_path / "svc" / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\n")
    (venv / "bin" / "rcm").write_text("#!/bin/sh\n")
    info = venv / "lib" / "python3.11" / "site-packages" / "remote_ci_monitor-0.0.0.dist-info"
    info.mkdir(parents=True)
    url = checkout.as_uri()
    (info / "direct_url.json").write_text(f'{{"url": "{url}", "dir_info": {{"editable": true}}}}')
    config = home / ".config" / "rcm" / "server.toml"
    config.parent.mkdir(parents=True)
    data = tmp_path / "prod-data"
    data.mkdir()
    config.write_text(f'[server]\ndata_dir = "{data}"\n')
    plist = home / "Library" / "LaunchAgents" / f"{SERVER_LABEL}.plist"
    plist.parent.mkdir(parents=True)
    doc = {
        "Label": SERVER_LABEL,
        "ProgramArguments": [str(venv / "bin" / "rcm"), "serve", "--config", str(config)],
    }
    plist.write_bytes(plistlib.dumps(doc))
    return {
        "checkout": checkout,
        "venv": venv,
        "config": config,
        "data": data,
        "plist": plist,
    }


def _nothing_written(cfg: Path, *paths: Path) -> None:
    assert not (cfg / "server.toml").exists() and not (cfg / "service").exists()
    for path in paths:
        assert not path.exists(), path


@pytest.mark.parametrize(
    ("finding", "what"),
    [
        # v1-1: 변수가 명령 자리(`R=rcm; $R setup …`) — 운영 data_dir 을 고른다
        ("v1-1", "data_dir"),
        # v1-2: `sed … | sh` 가 만든 명령 — 운영 설정 파일을 덮는다
        ("v1-2", "config"),
        # v1-3: 인터프리터가 만든 명령(`python3 -c 'os.system(…)'`) — 운영 venv 안에 쓴다
        ("v1-3", "venv"),
        # v1-4: 명령 낱말의 glob · `r(c)m` · 목록 밖 런처 — 운영 체크아웃 안에 서비스 파일을 쓴다
        ("v1-4", "checkout"),
    ],
)
def test_b9_352_setup_refuses_a_production_target_whatever_shell_built_it(
    cfg, tmp_path, prod, finding, what
):
    # 결정 352 · 353: 훅이 셸 글에서 놓친 명령이 setup 을 운영 쪽으로 돌려도 setup 자신이 쓰기
    # 직전에 거절한다(종료 1, 쓰지 않음, 우회 플래그 없음). 시험은 setup 을 그 인자로 직접 부른다
    argv = coordinator_args(tmp_path / "data")
    target: Path
    if what == "data_dir":
        target = prod["data"] / "inner"
        argv[argv.index("--data-dir") + 1] = str(target)
    elif what == "config":
        target = prod["config"]
        argv += ["--config", str(target), "--force", "all"]
    elif what == "venv":
        target = prod["venv"] / "etc" / "server.toml"
        argv += ["--config", str(target)]
    else:
        target = prod["checkout"] / "service" / "test.rcm.server.plist"
        argv += ["--service-file", str(target)]
    before = prod["config"].read_bytes()
    fake = Fake()
    assert go(fake, *argv) == 1, (finding, fake.text)
    err = fake.err.getvalue()
    assert f"this is {SERVER_LABEL}'s {what} — refusing" in err, (finding, err)
    assert "--force" not in err.split("refusing", 1)[1], err
    _nothing_written(cfg, tmp_path / "data")
    if what != "data_dir":
        assert not target.exists() or target == prod["config"], target
    assert prod["config"].read_bytes() == before


def test_b9_352_a_symlink_to_the_production_data_dir_is_refused(cfg, tmp_path, prod):
    # 심볼릭 링크를 풀어 견준다 — 링크가 운영 data_dir 을 가리키면 그 링크도 운영이다
    link = tmp_path / "looks-new"
    link.symlink_to(prod["data"])
    fake = Fake()
    assert go(fake, *coordinator_args(link / "sub")) == 1, fake.text
    assert f"this is {SERVER_LABEL}'s data_dir — refusing" in fake.err.getvalue()


def test_b9_352_darwin_compares_paths_without_case(cfg, tmp_path, prod):
    # darwin 은 대소문자를 접어 견준다
    other = Path(str(prod["data"]).upper().replace(str(tmp_path).upper(), str(tmp_path)))
    fake = Fake(platform="darwin")
    assert go(fake, *coordinator_args(other / "x")) == 1, fake.text
    assert "data_dir — refusing" in fake.err.getvalue()


def test_b9_352_the_production_label_needs_force_service_and_the_same_file(cfg, tmp_path, prod):
    # 라벨이 설치된 rcm 서비스와 같으면 `--force service` 이고 설치 파일이 setup 이 쓴 것과 같을
    # 때만 쓴다
    argv = coordinator_args(tmp_path / "data")
    argv[argv.index("--label") + 1] = SERVER_LABEL
    fake = Fake()
    assert go(fake, *argv) == 1, fake.text
    assert f"this is {SERVER_LABEL}'s label — refusing" in fake.err.getvalue()
    fake = Fake()
    assert go(fake, *argv, "--force", "service") == 1, fake.text
    assert "label — refusing" in fake.err.getvalue()
    _nothing_written(cfg)


def test_b9_352_setups_own_installed_service_still_guards_its_config(
    cfg, home, tmp_path, no_system_places
):
    # batch 10 H1(v1-1): 설치 파일이 setup 이 쓴 파일과 같아도(cmp) 그 서비스의 예외는 라벨 검사뿐
    # 이다. 그 서비스의 설정 · data_dir · venv · 체크아웃은 예외 없이 거절한다 — 같은 인자에
    # `--force all` 이면 설치된 서비스가 읽는 server.toml 을 덮었다(결정 352 「대상이 그중 하나와
    # 같거나 그 안이면 거절」)
    argv = coordinator_args(tmp_path / "data")
    assert go(Fake(), *argv) == 0
    staged, installed = _installed_and_staged(cfg, home, "darwin")
    installed.parent.mkdir(parents=True, exist_ok=True)
    installed.write_bytes(staged.read_bytes())
    config = cfg / "server.toml"
    before = config.read_bytes()
    config.write_bytes(before + b"# the installed service reads this\n")
    kept = config.read_bytes()
    fake = Fake()
    assert go(fake, *argv, "--force", "all") == 1, fake.text
    assert "this is test.rcm.server's config — refusing" in fake.err.getvalue(), fake.text
    assert config.read_bytes() == kept
    fake = Fake()
    assert go(fake, *argv) == 1, fake.text
    assert "this is test.rcm.server's" in fake.err.getvalue()
    # 같은 라벨인데 설치 파일이 다르다(다른 설정을 띄운다) — `--force` 로도 거절한다
    other = tmp_path / "other.toml"
    other.write_text(f'[server]\ndata_dir = "{tmp_path / "other-data"}"\n')
    doc = plistlib.loads(staged.read_bytes())
    doc["ProgramArguments"][-1] = str(other)
    installed.write_bytes(plistlib.dumps(doc))
    fake = Fake()
    assert go(fake, *argv, "--force", "all") == 1, fake.text
    assert "test.rcm.server's" in fake.err.getvalue() and "refusing" in fake.err.getvalue()
    assert config.read_bytes() == kept


@pytest.mark.parametrize(
    ("flag", "attr", "what"),
    [
        ("--config", "config", "config"),
        ("--data-dir", "data_dir", "data_dir"),
        ("--config", "venv", "venv"),
        ("--service-file", "checkout", "checkout"),
    ],
)
def test_b10_h1_setups_own_service_is_free_of_the_label_check_only(flag, attr, what):
    # batch 10 H1: setup 자신의 서비스(라벨 · `--force service` · 같은 바이트)라도 설정 · data_dir ·
    # venv · 체크아웃은 거절한다. 풀리는 것은 라벨 검사뿐이다
    svc = core.RcmService(
        name="lbl",
        file=Path("/s/lbl.plist"),
        names=("lbl",),
        config=Path("/c/server.toml"),
        data_dir=Path("/d"),
        venv=Path("/v"),
        checkout=Path("/k"),
        data=b"x",
    )
    own = dict(label="lbl", written=(b"x",), force_service=True, fold_case=False)
    own["installed"] = svc.file
    assert core.production_refusal([svc], {}, **own) is None
    target = getattr(svc, attr) / "inner" if attr != "config" else svc.config
    reason = core.production_refusal([svc], {flag: target}, **own)
    assert reason == f"{flag} {target}: this is lbl's {what} — refusing (nothing was written)"


@pytest.mark.parametrize(
    "body",
    [
        b"<plist><dict><key>Label",
        plistlib.dumps({"Label": "x", "ProgramArguments": ["/bin/sh", "-c", "exec rcm serve"]}),
        plistlib.dumps({"Label": "x", "ProgramArguments": ["/opt/v/bin/rcm", "serve"]}),
    ],
    ids=["unreadable", "sh-c", "no-config"],
)
def test_b9_352_an_rcm_service_setup_cannot_read_closes(
    cfg, home, tmp_path, no_system_places, body
):
    # 읽지 못하거나 `--config` 를 풀 수 없는 rcm 같은 유닛이 있으면 닫는 쪽이다 — 그 파일을 댄다
    plist = home / "Library" / "LaunchAgents" / "com.remote-ci-monitor.server.plist"
    plist.parent.mkdir(parents=True)
    plist.write_bytes(body)
    fake = Fake()
    assert go(fake, *coordinator_args(tmp_path / "data")) == 1, fake.text
    err = fake.err.getvalue()
    assert str(plist) in err and "refusing" in err, err
    _nothing_written(cfg, tmp_path / "data")


def test_b9_352_a_service_that_is_not_rcm_is_ignored(cfg, home, tmp_path, no_system_places):
    # rcm 이 아닌 서비스(rcm-ci-connect 의 ci-watch)는 운영이 아니다
    plist = home / "Library" / "LaunchAgents" / "com.remote-ci-monitor.ci-watch.plist"
    plist.parent.mkdir(parents=True)
    doc = {"Label": "com.remote-ci-monitor.ci-watch", "ProgramArguments": ["python3", "w.py"]}
    plist.write_bytes(plistlib.dumps(doc))
    fake = Fake()
    assert go(fake, *coordinator_args(tmp_path / "data")) == 0, fake.text


@pytest.mark.parametrize("where", ["home", "xdg", "system"])
def test_b9_352_systemd_units_in_every_supported_place_are_seen(
    cfg, home, tmp_path, no_system_places, where
):
    # systemd 사용자 자리(`~/.config/systemd/user`, `$XDG_CONFIG_HOME/systemd/user`)와 시스템 자리
    data = tmp_path / "unit-data"
    config = tmp_path / "unit-config" / "worker.toml"
    config.parent.mkdir(parents=True)
    config.write_text(f'[worker]\ndata_dir = "{data}"\n')
    folder = {
        "home": home / ".config" / "systemd" / "user",
        "xdg": tmp_path / "xdg-config" / "systemd" / "user",
        "system": no_system_places[2],
    }[where]
    folder.mkdir(parents=True)
    unit = folder / "rcm-worker.service"
    unit.write_text(f"[Service]\nExecStart=/opt/v/bin/rcm worker --config {config}\n")
    fake = Fake(platform="linux")
    assert go(fake, *coordinator_args(data / "x")) == 1, fake.text
    assert "rcm-worker's data_dir — refusing" in fake.err.getvalue()


def test_b9_352_a_unit_that_appears_after_the_scan_is_seen_before_writing(cfg, tmp_path, prod):
    # 스캔과 쓰기 사이에 생긴 유닛 — 쓰기 직전에 한 번 더 본다
    found = setup.scan_services()
    seen: list[int] = []

    def services():
        seen.append(1)
        return [] if len(seen) == 1 else found

    fake = Fake(services=services)
    argv = coordinator_args(prod["data"])
    assert go(fake, *argv) == 1, fake.text
    assert len(seen) == 2 and "data_dir — refusing" in fake.err.getvalue()
    _nothing_written(cfg)


def test_b9_352_core_refusal_is_pure():
    # core 는 모은 사실과 대상만 본다
    svc = core.RcmService(
        name="lbl",
        file=Path("/s/lbl.plist"),
        names=("lbl",),
        config=Path("/c/server.toml"),
        data_dir=Path("/d"),
        venv=Path("/v"),
        checkout=Path("/k"),
        data=b"x",
    )
    ask = dict(label=None, written=(), force_service=False, fold_case=False)
    assert core.production_refusal([svc], {"--data-dir": Path("/d/e")}, **ask) == (
        "--data-dir /d/e: this is lbl's data_dir — refusing (nothing was written)"
    )
    assert core.production_refusal([svc], {"--data-dir": Path("/dd")}, **ask) is None
    assert core.production_refusal([svc], {"--config": Path("/K/x")}, **ask) is None
    fold = {**ask, "fold_case": True}
    assert "checkout" in (core.production_refusal([svc], {"--config": Path("/K/x")}, **fold) or "")
    named = {**ask, "label": "lbl"}
    assert "label — refusing" in (core.production_refusal([svc], {}, **named) or "")
    forced = {**named, "force_service": True, "written": (b"x",), "installed": Path("/s/lbl.plist")}
    assert core.production_refusal([svc], {}, **forced) is None
    broken = core.RcmService(name="b", file=Path("/s/b.plist"), problem="cannot read it")
    assert "/s/b.plist" in (core.production_refusal([broken], {}, **ask) or "")


# ── P6 재확인(batch 10) ──


@pytest.mark.parametrize("manager", ["launchd", "systemd"])
def test_b10_h3_the_scan_reads_the_account_home_not_only_home(cfg, home, tmp_path, manager):
    # batch 10 H3(v1-3): `HOME=/tmp/x rcm setup --config <진짜 홈>/.config/rcm/server.toml --force
    # all` 은 `$HOME` 만 믿는 스캔을 지나 진짜 홈의 운영 설정을 덮었다. 스캔은 `$HOME` 과 passwd 의
    # 홈을 둘 다 본다(시험은 passwd 의 홈 `setup.account_home` 을 임시 폴더로 바꿔 끼운다)
    real = setup.account_home()
    found = _production(real, tmp_path)
    if manager == "systemd":
        # 실제 홈 아래의 기본 XDG 자리(`~/.config/systemd/user`) — `$XDG_CONFIG_HOME` 은 다른 곳이다
        found["plist"].unlink()
        unit = real / ".config" / "systemd" / "user" / "rcm-server.service"
        unit.parent.mkdir(parents=True)
        unit.write_text(
            f"[Service]\nExecStart={found['venv'] / 'bin' / 'rcm'} serve --config "
            f"{found['config']}\n"
        )
    assert real != home and os.environ["HOME"] == str(home)
    before = found["config"].read_bytes()
    argv = coordinator_args(tmp_path / "data", "--config", str(found["config"]), "--force", "all")
    fake = Fake(platform="darwin" if manager == "launchd" else "linux")
    assert go(fake, *argv) == 1, fake.text
    assert "config — refusing" in fake.err.getvalue(), fake.text
    assert found["config"].read_bytes() == before
    names = {svc.name for svc in setup.scan_services()}
    assert names == {SERVER_LABEL if manager == "launchd" else "rcm-server"}, names


def test_b10_m6_the_real_system_places_are_read_without_error(home, monkeypatch):
    # batch 10 M6: 진짜 시스템 자리를 읽는 시험은 이것 하나다 — 읽기만 하고 결과는 단언하지 않는다.
    # 사용자 자리는 임시 폴더다(HOME · passwd 홈)
    monkeypatch.setattr(setup, "SYSTEM_SERVICE_PLACES", REAL_SYSTEM_SERVICE_PLACES)
    found = setup.scan_services(environ={})
    assert isinstance(found, list)


def test_b10_h2_a_copy_outside_the_config_folder_does_not_make_the_label_setups(
    cfg, tmp_path, prod
):
    # batch 10 H2(v1-2 · v2-1): `written` 에 `--service-file` 이 가리키는 파일의 지금 바이트가
    # 들어갔다 — 운영 plist 의 사본을 가리키면 운영 라벨이 setup 의 것이 됐다. `written` 은 렌더한
    # 바이트와 설정 폴더 아래의 staged 사본뿐이다
    copy = tmp_path / "elsewhere" / "copy.plist"
    copy.parent.mkdir()
    copy.write_bytes(prod["plist"].read_bytes())
    argv = coordinator_args(tmp_path / "data", "--service-file", str(copy), "--force", "service")
    argv[argv.index("--label") + 1] = SERVER_LABEL
    fake = Fake()
    assert go(fake, *argv) == 1, fake.text
    assert f"this is {SERVER_LABEL}'s label — refusing" in fake.err.getvalue(), fake.text
    assert copy.read_bytes() == prod["plist"].read_bytes()
    _nothing_written(cfg, tmp_path / "data")


@pytest.mark.parametrize("how", ["file-link", "dir-link", "xdg", "config-dir-link"])
def test_b10_h2_an_install_place_is_found_through_links_and_xdg(cfg, home, tmp_path, prod, how):
    # batch 10 H2: 설치 자리는 심볼릭 링크를 풀어 견주고(`--service-file` 이 설치된 유닛을
    # 가리키는 링크, 설치 자리를 가리키는 폴더 링크) `$XDG_CONFIG_HOME/systemd/user` 도 설치 자리다
    agents = home / "Library" / "LaunchAgents"
    platform, flag = "darwin", "--service-file"
    if how == "file-link":
        target = tmp_path / "looks-staged.plist"
        target.symlink_to(prod["plist"])
    elif how in ("dir-link", "config-dir-link"):
        (tmp_path / "la").symlink_to(agents)
        target = tmp_path / "la" / ("x.toml" if how == "config-dir-link" else "new.plist")
        flag = "--config" if how == "config-dir-link" else flag
    else:
        platform = "linux"
        target = tmp_path / "xdg-config" / "systemd" / "user" / "test.rcm.server.service"
    before = sorted(p.name for p in agents.iterdir())
    plist = prod["plist"].read_bytes()
    argv = coordinator_args(tmp_path / "data", flag, str(target), "--force", "all")
    fake = Fake(platform=platform)
    assert go(fake, *argv) == 2, fake.text
    assert "an install place the service manager reads" in fake.err.getvalue(), fake.text
    assert sorted(p.name for p in agents.iterdir()) == before
    assert prod["plist"].read_bytes() == plist
    if how == "xdg":
        assert not target.exists()


def test_b10_h2_own_needs_the_install_place_and_the_service_file_is_refused_first():
    # batch 10 H2: setup 자신의 서비스는 설치 파일이 그 라벨의 설치 자리에 있을 때만이다. 해석된
    # `--service-file` 이 스캔한 서비스의 파일과 같으면 own 을 보기 전에 거절한다
    svc = core.RcmService(
        name="lbl", file=Path("/s/lbl.plist"), names=("lbl",), config=Path("/c/x.toml"), data=b"x"
    )
    own = dict(label="lbl", written=(b"x",), force_service=True, fold_case=False)
    assert core.production_refusal([svc], {}, installed=Path("/s/lbl.plist"), **own) is None
    elsewhere = core.production_refusal([svc], {}, installed=Path("/i/lbl.plist"), **own)
    assert "lbl's label — refusing" in (elsewhere or ""), elsewhere
    assert "lbl's label — refusing" in (core.production_refusal([svc], {}, **own) or "")
    same = {"--service-file": Path("/s/lbl.plist")}
    reason = core.production_refusal([svc], same, installed=Path("/s/lbl.plist"), **own)
    assert reason == (
        "--service-file /s/lbl.plist: this is lbl's service file — refusing (nothing was written)"
    )


@pytest.mark.parametrize("which", ["flag", "env", "config"])
def test_b10_m1_every_data_dir_a_unit_can_use_is_protected(cfg, home, tmp_path, which):
    # batch 10 M1(v1-4): 유닛의 `--data-dir` 플래그와 env `RCM_SERVER_DATA_DIR`, 설정의 data_dir 을
    # 모두 data_dir 로 본다 — 우선순위를 흉내 내지 않는다(어느 것이 이기는지는 rcm 판마다 다르다)
    dirs = {name: tmp_path / f"{name}-data" for name in ("flag", "env", "config")}
    config = tmp_path / "unit" / "server.toml"
    config.parent.mkdir()
    config.write_text(f'[server]\ndata_dir = "{dirs["config"]}"\n')
    plist = home / "Library" / "LaunchAgents" / f"{SERVER_LABEL}.plist"
    plist.parent.mkdir(parents=True)
    argv = ["/opt/v/bin/rcm", "serve", "--config", str(config), "--data-dir", str(dirs["flag"])]
    doc = {
        "Label": SERVER_LABEL,
        "ProgramArguments": argv,
        "EnvironmentVariables": {"RCM_SERVER_DATA_DIR": str(dirs["env"])},
    }
    plist.write_bytes(plistlib.dumps(doc))
    fake = Fake()
    assert go(fake, *coordinator_args(dirs[which] / "x")) == 1, fake.text
    assert f"this is {SERVER_LABEL}'s data_dir — refusing" in fake.err.getvalue(), fake.text
    _nothing_written(cfg, dirs[which] / "x")


@pytest.mark.parametrize("module", ["remote_ci_monitor.cli", "remote_ci_monitor"])
@pytest.mark.parametrize("manager", ["launchd", "systemd"])
def test_b10_m2_a_python_m_unit_names_the_venv_its_python_sits_in(
    cfg, home, tmp_path, module, manager
):
    # batch 10 M2(v1-5): `<venv>/bin/python -m remote_ci_monitor[.cli]` 의 `bin/python` 은 시스템
    # 파이썬으로 가는 심볼릭 링크다. 링크를 따라가면 venv 를 놓친다 — `bin/python` 이 있는 폴더의
    # 부모(`pyvenv.cfg`)가 그 서비스의 venv 다
    venv = tmp_path / "svc" / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\n")
    (venv / "bin" / "python").symlink_to(os.path.realpath(shutil.which("python3") or "/bin/sh"))
    config = tmp_path / "unit" / "server.toml"
    config.parent.mkdir()
    config.write_text(f'[server]\ndata_dir = "{tmp_path / "unit-data"}"\n')
    argv = [str(venv / "bin" / "python"), "-m", module, "serve", "--config", str(config)]
    if manager == "launchd":
        place = home / "Library" / "LaunchAgents" / f"{SERVER_LABEL}.plist"
        place.parent.mkdir(parents=True)
        place.write_bytes(plistlib.dumps({"Label": SERVER_LABEL, "ProgramArguments": argv}))
        name = SERVER_LABEL
    else:
        place = home / ".config" / "systemd" / "user" / "rcm-server.service"
        place.parent.mkdir(parents=True)
        place.write_text(f"[Service]\nExecStart={' '.join(argv)}\n")
        name = "rcm-server"
    target = venv / "etc" / "server.toml"
    fake = Fake(platform="darwin" if manager == "launchd" else "linux")
    assert go(fake, *coordinator_args(tmp_path / "data", "--config", str(target))) == 1, fake.text
    assert f"this is {name}'s venv — refusing" in fake.err.getvalue(), fake.text
    assert not target.exists()


@pytest.mark.parametrize(
    "line",
    [
        "EnvironmentFile=/etc/rcm/env",
        "Environment=RCM_SERVER_DATA_DIR=%S/rcm",
        'Environment="RCM_SERVER_DATA_DIR=/srv/rcm',
    ],
    ids=["environment-file", "specifier", "unreadable-line"],
)
def test_b10_m3_a_unit_with_lines_setup_does_not_follow_closes(cfg, home, tmp_path, line):
    # batch 10 M3(v1-6): 펼치지 못한 env(EnvironmentFile= · 지정자) · 읽지 못한 줄이 있는 rcm
    # 유닛은 그 안에 data_dir 이 있을 수 있다 — 닫는 쪽이다(거절 + 파일 이름)
    config = tmp_path / "unit" / "server.toml"
    config.parent.mkdir()
    config.write_text(f'[server]\ndata_dir = "{tmp_path / "unit-data"}"\n')
    unit = home / ".config" / "systemd" / "user" / "rcm-server.service"
    unit.parent.mkdir(parents=True)
    unit.write_text(f"[Service]\n{line}\nExecStart=/opt/v/bin/rcm serve --config {config}\n")
    fake = Fake(platform="linux")
    assert go(fake, *coordinator_args(tmp_path / "data")) == 1, fake.text
    err = fake.err.getvalue()
    assert str(unit) in err and "refusing" in err, err
    _nothing_written(cfg, tmp_path / "data")


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a file whatever its mode")
@pytest.mark.parametrize("where", ["file", "folder"])
def test_b10_l1_a_service_file_setup_may_not_read_closes_without_a_traceback(
    cfg, home, tmp_path, where
):
    # batch 10 L1(v1-9): 스캔의 `is_file()` · 읽기에서 난 PermissionError 는 traceback 이 아니라
    # 닫는 쪽이다 — 그 파일 이름을 대고 거절한다(그 안이 rcm 서비스인지 모른다)
    agents = home / "Library" / "LaunchAgents"
    agents.mkdir(parents=True)
    plist = agents / "com.example.thing.plist"
    plist.write_bytes(plistlib.dumps({"Label": "com.example.thing", "ProgramArguments": ["x"]}))
    locked = plist if where == "file" else agents
    locked.chmod(0o000 if where == "file" else 0o600)  # 폴더: 목록은 읽고 stat 은 못 한다
    try:
        fake = Fake()
        code = go(fake, *coordinator_args(tmp_path / "data"))
    finally:
        locked.chmod(0o644 if where == "file" else 0o755)
    err = fake.err.getvalue()
    assert code == 1, fake.text
    assert str(plist) in err and "refusing" in err and "unexpected" not in err, err
    _nothing_written(cfg, tmp_path / "data")


@pytest.mark.parametrize("platform", ["darwin", "linux"])
def test_b10_l2_the_remove_line_says_it_deletes_even_when_the_unload_fails(cfg, tmp_path, platform):
    # batch 10 L2: 제거 줄은 내리기가 실패해도 설치 파일을 지운다 — 바로 앞 줄이 그것을 말하고,
    # 서비스가 아직 돌고 있으면 먼저 멈추라고 한다. 제거 줄 자체는 셸에 그대로 붙일 수 있게 둔다
    fake = Fake(platform=platform)
    assert go(fake, *coordinator_args(tmp_path / "data")) == 0
    lines = _lines(fake)
    assert lines[-2].startswith("to remove: "), lines[-3:]
    assert lines[-3] == core.REMOVE_NOTE, lines[-3:]
    assert "even when the unload fails" in core.REMOVE_NOTE
    assert "stop it first" in core.REMOVE_NOTE
    fake = Fake()
    assert go(fake, "--role", "client", "--server", "http://10.0.0.5:1", "--yes") == 0
    assert core.REMOVE_NOTE not in _lines(fake)


def _server_venv(tmp_path: Path) -> Path:
    venv = tmp_path / "svc" / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\n")
    (venv / "bin" / "rcm").write_text("#!/bin/sh\n")
    return venv


@pytest.mark.parametrize("manager", ["launchd", "systemd"])
def test_b10_m5_a_relative_config_is_read_from_the_working_directory(cfg, home, tmp_path, manager):
    # batch 10 M5: 상대 `--config` 는 그 서비스의 WorkingDirectory 기준이다
    venv = _server_venv(tmp_path)
    workdir = tmp_path / "wd"
    config = workdir / "conf" / "server.toml"
    config.parent.mkdir(parents=True)
    config.write_text(f'[server]\ndata_dir = "{tmp_path / "unit-data"}"\n')
    rcm = venv / "bin" / "rcm"
    if manager == "launchd":
        place = home / "Library" / "LaunchAgents" / f"{SERVER_LABEL}.plist"
        place.parent.mkdir(parents=True)
        doc = {
            "Label": SERVER_LABEL,
            "ProgramArguments": [str(rcm), "serve", "--config", "conf/server.toml"],
            "WorkingDirectory": str(workdir),
        }
        place.write_bytes(plistlib.dumps(doc))
        name = SERVER_LABEL
    else:
        place = home / ".config" / "systemd" / "user" / "rcm-server.service"
        place.parent.mkdir(parents=True)
        place.write_text(
            f"[Service]\nWorkingDirectory={workdir}\nExecStart={rcm} serve --config "
            "conf/server.toml\n"
        )
        name = "rcm-server"
    before = config.read_bytes()
    fake = Fake(platform="darwin" if manager == "launchd" else "linux")
    argv = coordinator_args(tmp_path / "data", "--config", str(config), "--force", "all")
    assert go(fake, *argv) == 1, fake.text
    assert f"this is {name}'s config — refusing" in fake.err.getvalue(), fake.text
    assert config.read_bytes() == before


def test_b10_m5_systemd_quotes_and_percent_are_read_as_systemd_does(cfg, home, tmp_path):
    # batch 10 M5: ExecStart 의 따옴표와 `%%`(글자 `%`)를 systemd 처럼 풀어 견준다
    venv = _server_venv(tmp_path)
    config = tmp_path / "a b" / "100%" / "server.toml"
    config.parent.mkdir(parents=True)
    config.write_text(f'[server]\ndata_dir = "{tmp_path / "unit-data"}"\n')
    unit = home / ".config" / "systemd" / "user" / "rcm-server.service"
    unit.parent.mkdir(parents=True)
    spelled = str(config).replace("%", "%%")
    unit.write_text(f'[Service]\nExecStart={venv / "bin" / "rcm"} serve --config "{spelled}"\n')
    fake = Fake(platform="linux")
    argv = coordinator_args(tmp_path / "data", "--config", str(config), "--force", "all")
    assert go(fake, *argv) == 1, fake.text
    assert "this is rcm-server's config — refusing" in fake.err.getvalue(), fake.text


def test_b10_m5_a_symlinked_program_names_the_venv_it_points_into(cfg, home, tmp_path):
    # batch 10 M5: ProgramArguments[0] 이 venv 밖의 링크(`~/.local/bin/rcm`)면 링크를 푼 자리의
    # venv 가 그 서비스의 venv 다
    venv = _server_venv(tmp_path)
    link = home / ".local" / "bin" / "rcm"
    link.parent.mkdir(parents=True)
    link.symlink_to(venv / "bin" / "rcm")
    config = tmp_path / "unit" / "server.toml"
    config.parent.mkdir()
    config.write_text(f'[server]\ndata_dir = "{tmp_path / "unit-data"}"\n')
    plist = home / "Library" / "LaunchAgents" / f"{SERVER_LABEL}.plist"
    plist.parent.mkdir(parents=True)
    argv0 = [str(link), "serve", "--config", str(config)]
    plist.write_bytes(plistlib.dumps({"Label": SERVER_LABEL, "ProgramArguments": argv0}))
    target = venv / "etc" / "server.toml"
    fake = Fake()
    assert go(fake, *coordinator_args(tmp_path / "data", "--config", str(target))) == 1
    assert f"this is {SERVER_LABEL}'s venv — refusing" in fake.err.getvalue(), fake.text
